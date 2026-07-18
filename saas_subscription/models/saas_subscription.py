from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from odoo.tools import float_is_zero
from datetime import timedelta
from dateutil.relativedelta import relativedelta
import logging
import secrets
import base64

_logger = logging.getLogger(__name__)

class SaasSubscription(models.Model):
    _name = 'saas.subscription'
    _description = 'SaaS Subscription'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'
    _rec_name = 'display_name'

    # Basic Fields
    name = fields.Char(string='Subscription Reference', required=True, copy=False, readonly=True,
                       default=lambda self: _('New'))
    display_name = fields.Char(string='Display Name', compute='_compute_display_name', store=True)
    
    partner_id = fields.Many2one('res.partner', string='Customer', required=True, ondelete='restrict', tracking=True)
    package_id = fields.Many2one('saas.package', string='Package', required=True, ondelete='restrict', tracking=True)
    
    # State Management
    state = fields.Selection([
        ('draft', 'Draft'),
        ('pending', 'Pending Payment'),
        ('active', 'Active'),
        ('suspended', 'Suspended'),
        ('canceled', 'Canceled'),
        ('rejected', 'Rejected'),
        ('provisioning_failed', 'Provisioning Failed')
    ], string='Status', default='draft', tracking=True, required=True, copy=False)
    
    state_reason = fields.Text(string='State Reason', help='Reason for current state (e.g., payment failure, admin action)')
    
    # Tenant Information
    tenant_shortname = fields.Char(
        string='Company Short Form', copy=False,
        help='Customer-chosen short form (e.g. "DIU") used as the tenant '
             'subdomain/database prefix. Falls back to the company name if empty.')
    tenant_db_name = fields.Char(string='Tenant DB Name', copy=False)
    tenant_db_password = fields.Binary(string='Tenant DB Password', copy=False, help='Encrypted password')
    tenant_url = fields.Char(string='Tenant URL', copy=False)
    provisioned_at = fields.Datetime(string='Provisioned At', copy=False)
    provision_attempts = fields.Integer(string='Provisioning Attempts', default=0, copy=False)
    
    # Billing Information
    sale_order_id = fields.Many2one('sale.order', string='Sale Order', copy=False, readonly=True)
    invoice_ids = fields.One2many('account.move', compute='_compute_invoices', string='Invoices')
    
    billing_cycle = fields.Selection([
        ('monthly', 'Monthly'),
        ('yearly', 'Yearly')
    ], string='Billing Cycle', required=True, default='monthly')
    
    duration_months = fields.Integer(
        string='Duration (Months)', default=1,
        help='Commitment duration in months (1, 3, 6, 12). '
             'Determines discount tier and total price.')
    
    date_start = fields.Date(string='Start Date', default=fields.Date.today, tracking=True)
    date_next_invoice = fields.Date(string='Next Invoice Date', copy=False, tracking=True)
    date_end = fields.Date(string='End Date', copy=False, tracking=True)
    date_suspended = fields.Datetime(string='Suspended At', copy=False)
    date_canceled = fields.Datetime(string='Canceled At', copy=False)

    # Plan change (upgrade) & free trial
    upgrade_target_package_id = fields.Many2one(
        'saas.package', string='Upgrade Target', copy=False,
        help='Higher-tier package this subscription is upgrading to. Set while '
             'the upgrade payment is pending; cleared once the upgrade is applied.')
    upgrade_target_duration = fields.Integer(
        string='Upgrade Target Term (Months)', copy=False,
        help='The term the customer chose for the upgrade. If it differs from the '
             'current duration, applying the upgrade starts a fresh term of this '
             'length. Set while the upgrade payment is pending; cleared on apply.')
    module_sync_pending = fields.Boolean(
        string='Module Sync Pending', copy=False,
        help='An upgrade changed the package; the cron worker still needs to '
             'install the new apps into the existing tenant.')
    is_trial = fields.Boolean(string='Free Trial', copy=False, tracking=True)
    trial_end_date = fields.Date(string='Trial Ends', copy=False, tracking=True)
    trial_days_left = fields.Integer(
        string='Trial Days Left', compute='_compute_trial_days_left')

    # How the customer chose to pay: "Monthly" (base rate, billed every month)
    # vs a prepaid discounted term ("18-month term (-12%)"). Derived from
    # duration_months so the backend record shows exactly what was purchased.
    billing_plan_label = fields.Char(
        string='Billing Plan', compute='_compute_billing_plan_label',
        help='Monthly = base rate billed every month; an N-month term is the '
             'discounted rate paid in full upfront for that commitment.')

    # Payment Information — locked to SSLCommerz (single supported gateway).
    payment_gateway = fields.Selection(
        [('sslcommerz', 'SSLCommerz')],
        string='Payment Gateway', copy=False, default='sslcommerz', required=True,
        readonly=True,
        help='All SaaS subscription payments route through SSLCommerz only.'
    )
    
    # Points
    points_earned_total = fields.Integer(string='Total Points Earned', compute='_compute_points', store=False)
    points_redeemed_total = fields.Integer(string='Total Points Redeemed', compute='_compute_points', store=False)
    points_balance = fields.Integer(string='Points Balance', compute='_compute_points', store=False)
    
    # Logs
    log_ids = fields.One2many('saas.subscription.log', 'subscription_id', string='State Change Logs')
    
    # Company
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)
    
    # User count tracking (for future)
    current_user_count = fields.Integer(string='Current Users', default=0, help='Number of users in tenant instance')
    
    @api.depends('name', 'partner_id', 'state')
    def _compute_display_name(self):
        for sub in self:
            sub.display_name = f"{sub.name} - {sub.partner_id.name} ({sub.state})"
    
    def _compute_invoices(self):
        for sub in self:
            domain = [('move_type', '=', 'out_invoice')]
            or_terms = []

            # Prefer direct link if saas_points is installed.
            if 'saas_subscription_id' in self.env['account.move']._fields:
                or_terms.append(('saas_subscription_id', '=', sub.id))

            if sub.sale_order_id:
                or_terms.append(('invoice_origin', '=', sub.sale_order_id.name))

            # Fallback for renewal/legacy invoices that embed subscription ref.
            or_terms.append(('invoice_origin', 'ilike', sub.name))

            if len(or_terms) == 1:
                domain += or_terms
            elif len(or_terms) == 2:
                domain += ['|'] + or_terms
            else:
                domain += ['|', or_terms[0], '|', or_terms[1], or_terms[2]]

            sub.invoice_ids = self.env['account.move'].search(domain)
    
    def _compute_points(self):
        for sub in self:
            if 'saas.points.transaction' in self.env:
                transactions = self.env['saas.points.transaction'].search([
                    ('subscription_id', '=', sub.id)
                ])
                sub.points_earned_total = sum(t.points for t in transactions if t.points > 0)
                sub.points_redeemed_total = abs(sum(t.points for t in transactions if t.points < 0 and t.transaction_type == 'redeem'))
                sub.points_balance = sub.points_earned_total - sub.points_redeemed_total
            else:
                sub.points_earned_total = 0
                sub.points_redeemed_total = 0
                sub.points_balance = 0

    def _compute_trial_days_left(self):
        today = fields.Date.today()
        for sub in self:
            if sub.is_trial and sub.trial_end_date:
                sub.trial_days_left = max(0, (sub.trial_end_date - today).days)
            else:
                sub.trial_days_left = 0

    @api.depends('duration_months', 'package_id')
    def _compute_billing_plan_label(self):
        for sub in self:
            months = sub.duration_months or 1
            if months <= 1:
                sub.billing_plan_label = 'Monthly'
                continue
            discount = 0.0
            if sub.package_id:
                discount = sub.package_id.get_duration_pricing(months).get('discount_percent', 0.0)
            if discount:
                sub.billing_plan_label = '%d-month term (-%g%%)' % (months, discount)
            else:
                sub.billing_plan_label = '%d-month term' % months

    # ==================== UPGRADE (PLAN CHANGE) ====================

    def _get_upgrade_targets(self):
        """Active packages this subscription can upgrade to (strictly higher tier)."""
        self.ensure_one()
        if not self.package_id:
            return self.env['saas.package']
        return self.env['saas.package'].search([
            ('active', '=', True),
            ('tier_level', '>', self.package_id.tier_level),
        ], order='tier_level, monthly_price')

    def _remaining_term_months(self):
        """Months still unused in the current committed term, from the TRUE term
        span (start + committed months) — not date_next_invoice, which can be
        stale on older subs and would skew the maths. Clamped to [0, duration]."""
        self.ensure_one()
        duration = self.duration_months or 1
        today = fields.Date.today()
        term_end = (self.date_start or today) + relativedelta(months=duration)
        days_left = (term_end - today).days
        return max(0.0, min(float(duration), days_left / 30.0))

    def _compute_upgrade_price(self, target_package, new_duration=None):
        """Amount to pay now to upgrade to ``target_package``.

        Two cases, both "pay only the accurate difference":

        * **Keep the current term** (``new_duration`` == current, the default):
          pay just the prorated *tier difference* for the months left — the
          discounted monthly rates for the committed duration times months left.
          Term length and renewal date are unchanged; renewals then bill the new
          tier at the same term.

        * **Switch to a different (longer) term**: start a fresh
          ``new_duration``-month term of the target, crediting the unused value
          already paid on the current term. So the top-up is the target's full
          term price for the new duration minus that remaining credit
          (e.g. 12-mo Essential → 18-mo Professional = Prof-18mo-total − what's
          left of the Essential payment).

        Returns 0.0 when nothing is owed.
        """
        self.ensure_one()
        cur_duration = self.duration_months or 1
        new_duration = int(new_duration) if new_duration else cur_duration
        months_left = self._remaining_term_months()
        cur_monthly = self.package_id.get_duration_pricing(cur_duration).get(
            'monthly_price', self.package_id.monthly_price) or 0.0

        if new_duration == cur_duration:
            # Keep term: prorated tier difference for the remaining months.
            tgt_monthly = target_package.get_duration_pricing(cur_duration).get(
                'monthly_price', target_package.monthly_price) or 0.0
            diff_per_month = tgt_monthly - cur_monthly
            if diff_per_month <= 0:
                return 0.0
            return round(diff_per_month * months_left, 2)

        # Change term: fresh <new_duration> term of the target, less the unused
        # value already paid on the current term.
        target_full = target_package.get_duration_pricing(new_duration).get('total_price', 0.0) or 0.0
        remaining_credit = cur_monthly * months_left
        return round(max(0.0, target_full - remaining_credit), 2)

    def action_apply_upgrade(self, target_package, new_duration=None):
        """Switch this subscription to a higher-tier package and queue the tenant
        app install (billing is handled by the caller). The tenant DB is
        unchanged — the cron worker installs the extra modules additively.

        If ``new_duration`` differs from the current term, the term is reset to a
        fresh ``new_duration``-month term starting today; otherwise the term and
        renewal date are kept (only healed to their true end)."""
        self.ensure_one()
        old_pkg = self.package_id
        cur_duration = self.duration_months or 1
        new_duration = int(new_duration) if new_duration else cur_duration
        today = fields.Date.today()
        vals = {
            'package_id': target_package.id,
            'module_sync_pending': True,
            'upgrade_target_package_id': False,
            'upgrade_target_duration': 0,
        }
        if new_duration != cur_duration:
            # New term: start it now so date_start + duration == next invoice.
            vals['duration_months'] = new_duration
            vals['date_start'] = today
            vals['date_next_invoice'] = today + relativedelta(months=new_duration)
        else:
            # Keep the SAME term; pin the renewal date to its true end (no-op for
            # healthy subs; heals any legacy/stale date so renewals bill on time).
            vals['date_next_invoice'] = (self.date_start or today) + relativedelta(months=cur_duration)
        self.write(vals)
        term_note = (' (term changed to %d months)' % new_duration) if new_duration != cur_duration else ''
        self._log_state_change(
            self.state, self.state,
            'Upgraded plan: %s → %s%s' % (old_pkg.name, target_package.name, term_note))
        # Install the new apps in the cron worker (long-running, survives limits).
        cron = self.env.ref('saas_subscription.cron_sync_tenant_modules',
                            raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()
        else:
            _logger.error("Module-sync cron not found; upgrade apps for %s "
                          "won't install until the cron runs.", self.name)
        return True

    # ==================== FREE TRIAL ====================

    def _convert_trial_to_paid(self):
        """Turn an in-trial subscription into a normal paid one. Billing-only —
        the tenant already exists, so no re-provisioning happens."""
        self.ensure_one()
        if not self.is_trial:
            return
        self.write({
            'is_trial': False,
            'trial_end_date': False,
            'date_next_invoice': fields.Date.today() + relativedelta(months=self.duration_months or 1),
        })
        self._log_state_change(self.state, self.state, 'Free trial converted to paid')
        self._push_tenant_snapshot()

    # ==================== TENANT DASHBOARD SNAPSHOT ====================

    def _push_tenant_snapshot(self):
        """Refresh the in-tenant dashboard snapshot for this subscription's tenant.
        No-op if the tenant isn't provisioned yet. Safe to call after any change
        that the customer should see (upgrade, renewal, suspend, convert)."""
        provisioner = self.env['tenant.provisioner']
        for sub in self:
            if not sub.tenant_db_name:
                continue
            try:
                provisioner._store_subscription_snapshot(sub.tenant_db_name, sub)
            except Exception as e:
                _logger.warning("Snapshot push failed for %s: %s", sub.name, e)

    @api.model
    def _cron_refresh_tenant_snapshots(self):
        """Daily: re-push the dashboard snapshot to every provisioned tenant so
        status, renewal date and days-left stay current (storage usage itself is
        computed live inside each tenant)."""
        subs = self.search([('tenant_db_name', '!=', False),
                            ('state', 'in', ('active', 'suspended'))])
        subs._push_tenant_snapshot()
        _logger.info("Refreshed dashboard snapshot for %d tenant(s)", len(subs))
        return True

    @api.model
    def _cron_expire_trials(self):
        """Suspend tenants whose free trial has ended without payment (data kept),
        and email the customer. The auto-cancel-suspended cron later cleans up
        trials that never convert."""
        today = fields.Date.today()
        expired = self.search([
            ('is_trial', '=', True),
            ('state', '=', 'active'),
            ('trial_end_date', '!=', False),
            ('trial_end_date', '<=', today),
        ])
        for sub in expired:
            try:
                sub.action_suspend()
                sub.write({'state_reason': 'Free trial ended — awaiting payment.'})
                sub._send_state_email('suspended')
            except Exception as e:
                _logger.warning("Trial expiry failed for %s: %s", sub.name, e)
        if expired:
            _logger.info("Expired %d trial subscription(s)", len(expired))
        return True

    @api.model
    def create(self, vals):
        if vals.get('name', _('New')) == _('New'):
            vals['name'] = self.env['ir.sequence'].next_by_code('saas.subscription') or _('New')
        
        # Next invoice = end of the prepaid term. The customer pays the full
        # duration up front (e.g. 18 months), so they're not re-billed until the
        # term ends — NOT every 30 days.
        if 'date_next_invoice' not in vals:
            months = int(vals.get('duration_months') or 1)
            vals['date_next_invoice'] = fields.Date.today() + relativedelta(months=months)
        
        subscription = super(SaasSubscription, self).create(vals)
        
        # Send welcome email for draft
        if vals.get('state') == 'draft':
            subscription._send_state_email('draft')
        
        return subscription
    
    def write(self, vals):
        # Track state changes for logging
        old_states = {sub.id: sub.state for sub in self}
        result = super(SaasSubscription, self).write(vals)
        
        if 'state' in vals:
            for sub in self:
                old_state = old_states.get(sub.id)
                new_state = vals['state']
                if old_state != new_state:
                    try:
                        sub._log_state_change(old_state, new_state, vals.get('state_reason', 'State changed via write'))
                    except Exception as e:
                        _logger.warning(f"Failed to log state change for {sub.name}: {e}")
                    
                    try:
                        sub._send_state_email(new_state)
                    except Exception as e:
                        _logger.warning(f"Failed to send state email for {sub.name}: {e}")
                    
                    # Trigger provisioning on activation — but only if
                    # no tenant DB has been created yet (skip for reactivation
                    # from suspended, which already has a running tenant).
                    #
                    # IMPORTANT: provisioning runs in a BACKGROUND THREAD with
                    # its own cursor so it does NOT block this transaction from
                    # committing.  The activation state change is saved
                    # immediately; provisioning happens asynchronously.
                    if new_state == 'active' and old_state in ['pending', 'provisioning_failed']:
                        if not sub.tenant_db_name:
                            self._schedule_provisioning(sub.id)
        
        return result
    
    def _schedule_provisioning(self, subscription_id):
        """Queue tenant provisioning to run in the cron worker.

        Provisioning is long and MUST outlive the current HTTP request. An
        in-request background thread is NOT reliable: it intermittently fails to
        even acquire the registry (odoo.registry(db_name) raising at thread
        start), and if the web worker is recycled mid-install the psql-created
        tenant DB survives but the ORM write-back of tenant_db_name / tenant_url
        never commits — leaving an orphan DB and a subscription stuck 'active'
        with empty tenant fields.

        Instead we ask Odoo's scheduler to run the provisioning cron as soon as
        possible. The cron worker is not bound by web-request time limits and
        commits its own transaction, so the write-back is reliable. The same
        cron also runs periodically as a backstop and self-heals any stranded
        subscription.
        """
        cron = self.env.ref(
            'saas_subscription.cron_retry_failed_provisioning',
            raise_if_not_found=False,
        )
        if cron:
            cron.sudo()._trigger()  # schedule an (almost) immediate one-off run
            _logger.info(
                "Queued provisioning for subscription ID %s via cron trigger",
                subscription_id)
        else:
            _logger.error(
                "Provisioning cron not found; cannot queue provisioning for "
                "subscription ID %s. Trigger the retry cron manually.",
                subscription_id)
    
    # ==================== STATE TRANSITION METHODS ====================
    
    def action_confirm(self):
        """Move from draft to pending payment"""
        for record in self:
            if record.state != 'draft':
                raise UserError(_('Only draft subscriptions can be confirmed.'))
            
            # Validate required fields
            if not record.partner_id:
                raise UserError(_('Please select a customer before confirming.'))
            if not record.package_id:
                raise UserError(_('Please select a package before confirming.'))
            
            # Create sale order
            sale_order = self._create_sale_order()
            record.write({
                'state': 'pending',
                'sale_order_id': sale_order.id,
                'date_next_invoice': fields.Date.today() + relativedelta(months=record.duration_months or 1),
                'state_reason': 'Subscription confirmed by user',
            })
            # Note: _log_state_change is called automatically by write() override
    
    def action_activate(self):
        """Activate subscription (manual override or after payment)"""
        for record in self:
            if record.state not in ['pending', 'suspended', 'provisioning_failed']:
                raise UserError(_('Only pending, suspended, or provisioning_failed subscriptions can be activated.'))
            
            record.write({
                'state': 'active',
                'date_start': fields.Date.today(),
                'state_reason': False
            })
    
    def action_suspend(self):
        """Suspend subscription (block tenant access)"""
        for record in self:
            if record.state != 'active':
                raise UserError(_('Only active subscriptions can be suspended.'))
            
            record.write({
                'state': 'suspended',
                'date_suspended': fields.Datetime.now(),
                'state_reason': 'Manually suspended by admin'
            })
    
    def action_cancel(self):
        """Cancel subscription immediately"""
        for record in self:
            if record.state in ['canceled', 'rejected']:
                raise UserError(_('Subscription is already terminated.'))
            
            record.write({
                'state': 'canceled',
                'date_end': fields.Date.today(),
                'date_canceled': fields.Datetime.now(),
                'state_reason': 'Cancelled by user request'
            })
            
            # TODO: Schedule tenant deletion (Phase 3)
    
    def action_reject(self):
        """Reject subscription (e.g., fraud detection)"""
        for record in self:
            if record.state != 'pending':
                raise UserError(_('Only pending subscriptions can be rejected.'))
            
            record.write({
                'state': 'rejected',
                'state_reason': 'Payment rejected or fraud detected',
                'date_end': fields.Date.today()
            })
    
    def action_retry_provisioning(self):
        """Retry failed provisioning"""
        for record in self:
            if record.state != 'provisioning_failed':
                raise UserError(_('Only failed provisioning subscriptions can be retried.'))
            
            record.write({'state': 'pending'})
            # Will be re-activated by payment check
    
    def action_force_provision(self):
        """Admin: (re)provision this tenant now.

        Also covers the case where a subscription is already 'active' but its
        tenant was never created (e.g. the in-request background provisioning
        thread died before finishing). The 'Retry Provisioning' action only
        handles the 'provisioning_failed' state, so this fills that gap.
        Provisioning runs in the background; the retry cron is the backstop.
        """
        self.ensure_one()
        if self.tenant_db_name:
            raise UserError(_('This subscription already has a tenant: %s') % self.tenant_db_name)
        if self.state in ('pending', 'provisioning_failed'):
            # Writing to 'active' schedules provisioning via the write() override.
            self.write({'state': 'active'})
        elif self.state == 'active':
            # Already active but unprovisioned — schedule it directly.
            self._schedule_provisioning(self.id)
        else:
            raise UserError(_('Provisioning can only be forced for pending, active, or failed subscriptions.'))
        return True
    
    # ==================== HELPER METHODS ====================
    
    def _create_sale_order(self):
        """Create sale order for subscription.
        
        Uses get_duration_pricing() to calculate the correct total
        based on duration_months (includes discount tiers).
        """
        self.ensure_one()
        
        # Use duration-aware pricing
        duration = self.duration_months or 1
        pricing = self.package_id.get_duration_pricing(duration)
        total_price = pricing['total_price']
        monthly_price = pricing['monthly_price']
        discount_pct = pricing.get('discount_percent', 0)
        
        # Build descriptive line name
        if duration > 1:
            line_name = (
                f"{self.package_id.name} — {duration} Months"
            )
            if discount_pct:
                line_name += f" ({discount_pct}% off)"
        else:
            line_name = (
                f"{self.package_id.name} — Monthly Subscription"
            )
        
        # Create sale order
        sale_order = self.env['sale.order'].create({
            'partner_id': self.partner_id.id,
            'company_id': self.company_id.id,
            'origin': self.name,
            'note': f"SaaS Subscription: {self.package_id.name} "
                    f"({duration} month{'s' if duration > 1 else ''})",
        })
        
        # Create order line with full total price
        self.env['sale.order.line'].create({
            'order_id': sale_order.id,
            'product_id': self._get_or_create_product().id,
            'product_uom_qty': 1,
            'price_unit': total_price,
            'name': line_name,
        })
        
        sale_order.action_confirm()
        return sale_order
    
    def _get_or_create_product(self):
        """Get or create service product for SaaS subscription"""
        product = self.env['product.product'].search([
            ('name', '=', 'SaaS Subscription'),
            ('type', '=', 'service')
        ], limit=1)
        
        if not product:
            # Odoo 18: 'service_type' was removed; 'invoice_policy' lives on
            # product.template but is not always writable via product.product.
            product = self.env['product.product'].create({
                'name': 'SaaS Subscription',
                'type': 'service',
                'list_price': 0.0,
            })
        
        return product
    
    # _trigger_provisioning is implemented in tenant_provisioner.py (inherited override).
    # The override replaces this placeholder via Odoo's _inherit MRO.

    def get_base_url(self):
        """Get base URL for the SaaS platform"""
        domain = self.env['ir.config_parameter'].sudo().get_param('saas.domain_base', 'localhost')
        return f"http://{domain}"
    
    def _log_state_change(self, from_state, to_state, reason):
        """Create log entry for state change"""
        self.env['saas.subscription.log'].create({
            'subscription_id': self.id,
            'from_state': from_state,
            'to_state': to_state,
            'reason': reason or 'State changed',
            'user_id': self.env.user.id
        })
    
    def _send_state_email(self, state):
        """Send email notification based on state"""
        template_map = {
            'active': 'saas_subscription.email_template_subscription_active',
            'suspended': 'saas_subscription.email_template_subscription_suspended',
            'canceled': 'saas_subscription.email_template_subscription_canceled',
            'rejected': 'saas_subscription.email_template_subscription_rejected',
            'provisioning_failed': 'saas_subscription.email_template_provisioning_failed',
        }
        
        template_xml_id = template_map.get(state)
        if template_xml_id:
            try:
                template = self.env.ref(template_xml_id)
                template.send_mail(self.id, force_send=True)
            except Exception as e:
                _logger.warning(f"Failed to send email for subscription {self.name}: {e}")
    
    def _encrypt_password(self, password):
        """Encrypt tenant DB password using Fernet"""
        from cryptography.fernet import Fernet
        # Get encryption key from system parameter or generate
        key_param = self.env['ir.config_parameter'].sudo().get_param('saas.encryption_key')
        if not key_param:
            # Generate and store key (first run)
            key = Fernet.generate_key()
            self.env['ir.config_parameter'].sudo().set_param('saas.encryption_key', key.decode())
            key_param = key.decode()
        
        fernet = Fernet(key_param.encode())
        encrypted = fernet.encrypt(password.encode())
        return base64.b64encode(encrypted)
    
    def _decrypt_password(self, encrypted_password):
        """Decrypt tenant DB password"""
        from cryptography.fernet import Fernet
        key_param = self.env['ir.config_parameter'].sudo().get_param('saas.encryption_key')
        if not key_param:
            return None
        
        fernet = Fernet(key_param.encode())
        decrypted = fernet.decrypt(base64.b64decode(encrypted_password))
        return decrypted.decode()
    
    # ==================== ACTION METHODS FOR PORTAL ====================
    
    def action_view_invoices(self):
        """View all invoices for this subscription"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Invoices',
            'res_model': 'account.move',
            'view_mode': 'tree,form',
            'domain': [('id', 'in', self.invoice_ids.ids)],
            'target': 'current',
        }
    
    def action_pay_now(self):
        """Redirect to payment gateway checkout"""
        self.ensure_one()
        
        return {
            'type': 'ir.actions.act_url',
            'url': f'/saas/payment/checkout?subscription_id={self.id}',
            'target': 'self',
        }
    
    def action_renew(self):
        """Manually renew subscription"""
        self.ensure_one()
        if self.state != 'active':
            raise UserError(_('Only active subscriptions can be renewed.'))
        
        # Reset next invoice date by the committed term length.
        self.date_next_invoice = fields.Date.today() + relativedelta(months=self.duration_months or 1)

        self._log_state_change(self.state, self.state, 'Manual renewal triggered')
