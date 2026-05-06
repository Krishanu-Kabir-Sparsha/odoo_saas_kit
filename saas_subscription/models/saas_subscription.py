from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from odoo.tools import float_is_zero
from datetime import timedelta
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
    
    date_start = fields.Date(string='Start Date', default=fields.Date.today, tracking=True)
    date_next_invoice = fields.Date(string='Next Invoice Date', copy=False, tracking=True)
    date_end = fields.Date(string='End Date', copy=False, tracking=True)
    date_suspended = fields.Datetime(string='Suspended At', copy=False)
    date_canceled = fields.Datetime(string='Canceled At', copy=False)
    
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
    
    @api.model
    def create(self, vals):
        if vals.get('name', _('New')) == _('New'):
            vals['name'] = self.env['ir.sequence'].next_by_code('saas.subscription') or _('New')
        
        # Set next invoice date based on billing cycle
        if 'date_next_invoice' not in vals:
            if vals.get('billing_cycle') == 'yearly':
                vals['date_next_invoice'] = fields.Date.today() + timedelta(days=365)
            else:
                vals['date_next_invoice'] = fields.Date.today() + timedelta(days=30)
        
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
                    sub._log_state_change(old_state, new_state, vals.get('state_reason', 'State changed via write'))
                    sub._send_state_email(new_state)
                    
                    # Trigger provisioning on activation
                    if new_state == 'active' and old_state in ['pending', 'suspended']:
                        sub._trigger_provisioning()
        
        return result
    
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
                'date_next_invoice': fields.Date.today() + timedelta(days=30 if record.billing_cycle == 'monthly' else 365)
            })
            
            record._log_state_change('draft', 'pending', 'Subscription confirmed by user')
    
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
        """Admin: Force provision tenant without payment"""
        self.ensure_one()
        if self.state not in ['pending', 'provisioning_failed']:
            raise UserError(_('Provisioning can only be forced for pending or failed subscriptions.'))
        
        self.write({'state': 'active'})
        # Provisioning triggered via write method
    
    # ==================== HELPER METHODS ====================
    
    def _create_sale_order(self):
        """Create sale order for subscription"""
        self.ensure_one()
        
        # Get price for selected billing cycle
        if self.billing_cycle == 'yearly':
            price = self.package_id.yearly_price
        else:
            price = self.package_id.monthly_price
        
        # Create sale order
        sale_order = self.env['sale.order'].create({
            'partner_id': self.partner_id.id,
            'company_id': self.company_id.id,
            'origin': self.name,
            'note': f"SaaS Subscription: {self.package_id.name} ({self.billing_cycle})",
        })
        
        # Create order line
        self.env['sale.order.line'].create({
            'order_id': sale_order.id,
            'product_id': self._get_or_create_product().id,
            'product_uom_qty': 1,
            'price_unit': price,
            'name': f"{self.package_id.name} - {self.billing_cycle.capitalize()} Subscription",
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
            product = self.env['product.product'].create({
                'name': 'SaaS Subscription',
                'type': 'service',
                'invoice_policy': 'order',
                'service_type': 'manual',
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
        
        # Reset next invoice date
        if self.billing_cycle == 'yearly':
            self.date_next_invoice = fields.Date.today() + timedelta(days=365)
        else:
            self.date_next_invoice = fields.Date.today() + timedelta(days=30)
        
        self._log_state_change(self.state, self.state, 'Manual renewal triggered')
