from odoo import models, fields, api, _
from datetime import timedelta
import logging

_logger = logging.getLogger(__name__)

class SaasDunningProcess(models.Model):
    _name = 'saas.dunning.process'
    _description = 'SaaS Dunning Process for Overdue Invoices'
    _rec_name = 'subscription_id'

    subscription_id = fields.Many2one('saas.subscription', string='Subscription', required=True)
    invoice_id = fields.Many2one('account.move', string='Overdue Invoice', required=True)
    days_overdue = fields.Integer(string='Days Overdue', compute='_compute_days_overdue')
    dunning_level = fields.Selection([
        ('level_1', 'Reminder 1 - Day 2'),
        ('level_2', 'Reminder 2 - Day 5'),
        ('level_3', 'Final Warning - Day 8'),
        ('suspended', 'Suspended - Day 9+')
    ], string='Dunning Level', default='level_1')
    last_notification_date = fields.Datetime(string='Last Notification Date')
    late_fee_applied = fields.Boolean(string='Late Fee Applied', default=False)
    late_fee_invoice_id = fields.Many2one('account.move', string='Late Fee Invoice')
    state = fields.Selection([
        ('active', 'Active'),
        ('resolved', 'Resolved'),
        ('escalated', 'Escalated')
    ], string='Status', default='active')

    @api.depends('invoice_id')
    def _compute_days_overdue(self):
        for record in self:
            if record.invoice_id and record.invoice_id.invoice_date_due:
                due_date = record.invoice_id.invoice_date_due
                today = fields.Date.today()
                if due_date < today:
                    record.days_overdue = (today - due_date).days
                else:
                    record.days_overdue = 0
            else:
                record.days_overdue = 0

    @api.model
    def _cron_process_dunning(self):
        """Cron job: Process dunning for overdue invoices"""
        _logger.info("Starting dunning process cron job")
        
        today = fields.Date.today()
        
        # Find all unpaid invoices for active subscriptions
        # Note: Using search for related invoices
        invoices = self.env['account.move'].search([
            ('move_type', '=', 'out_invoice'),
            ('payment_state', 'in', ['not_paid', 'partial']),
            ('invoice_date_due', '<', today),
            ('state', '=', 'posted')
        ])
        
        processed_count = 0
        suspended_count = 0
        
        for invoice in invoices:
            # Prefer the direct link added by saas_points (account.move.saas_subscription_id).
            # Fall back to invoice_origin text match for legacy invoices.
            subscription = invoice.saas_subscription_id
            if not subscription:
                subscription = self.env['saas.subscription'].search([
                    ('name', '=', invoice.invoice_origin)
                ], limit=1)

            if not subscription or subscription.state != 'active':
                continue
            
            due_date = invoice.invoice_date_due
            days_overdue = (today - due_date).days
            
            # Get or create dunning record
            dunning = self.search([
                ('subscription_id', '=', subscription.id),
                ('invoice_id', '=', invoice.id),
                ('state', '=', 'active')
            ], limit=1)
            
            if not dunning:
                dunning = self.create({
                    'subscription_id': subscription.id,
                    'invoice_id': invoice.id,
                })
            
            # Process based on days overdue
            if days_overdue >= 9 and dunning.dunning_level != 'suspended':
                # Suspension
                _logger.info(f"Suspending subscription {subscription.name} due to non-payment (days: {days_overdue})")
                subscription.action_suspend()
                dunning.write({
                    'dunning_level': 'suspended',
                    'state': 'escalated'
                })
                suspended_count += 1
                
                # Send suspension email
                dunning._send_dunning_email('suspension')
                
            elif days_overdue >= 8 and dunning.dunning_level != 'level_3':
                # Final warning
                dunning.write({'dunning_level': 'level_3', 'last_notification_date': fields.Datetime.now()})
                dunning._send_dunning_email('final_warning')
                
            elif days_overdue >= 5 and dunning.dunning_level not in ['level_2', 'level_3', 'suspended']:
                # Second reminder + late fee
                dunning.write({'dunning_level': 'level_2', 'last_notification_date': fields.Datetime.now()})
                dunning._apply_late_fee()
                dunning._send_dunning_email('reminder_2')
                
            elif days_overdue >= 2 and dunning.dunning_level == 'level_1':
                # First reminder
                dunning.write({'dunning_level': 'level_1', 'last_notification_date': fields.Datetime.now()})
                dunning._send_dunning_email('reminder_1')
            
            processed_count += 1
        
        _logger.info(f"Dunning process completed: {processed_count} processed, {suspended_count} suspended")
        return True

    def _apply_late_fee(self):
        """Issue a separate late-fee invoice (we never mutate the posted original).

        Modifying a posted invoice is unsafe in Odoo accounting (breaks reconciliation
        and audit trail). Instead we always create a brand-new posted invoice for the
        fee amount, linked back to the original via invoice_origin and to the same
        subscription via saas_subscription_id (so dunning/payment flows can find it).
        """
        self.ensure_one()
        if self.late_fee_applied:
            return

        late_fee_percent = float(self.env['ir.config_parameter'].sudo().get_param('saas.late_fee_percent', '5'))
        invoice = self.invoice_id
        late_fee_amount = invoice.amount_total * (late_fee_percent / 100)

        late_fee_invoice = self._create_late_fee_invoice(invoice, late_fee_amount)

        self.write({
            'late_fee_applied': True,
            'late_fee_invoice_id': late_fee_invoice.id,
        })
        _logger.info(
            f"Issued separate late-fee invoice {late_fee_invoice.name} "
            f"({late_fee_amount}) for original {invoice.name}"
        )

    def _get_late_fee_product(self):
        """Get or create late fee product"""
        product = self.env['product.product'].search([
            ('name', '=', 'Late Fee'),
            ('type', '=', 'service')
        ], limit=1)
        
        if not product:
            product = self.env['product.product'].create({
                'name': 'Late Fee',
                'type': 'service',
                'list_price': 0.0,
            })
        return product

    def _create_late_fee_invoice(self, original_invoice, amount):
        """Create + post a separate invoice for the late fee."""
        product = self._get_late_fee_product()
        # account.move expects invoice_line_ids on create; building lines after the
        # fact via account.move.line.create skips Odoo's tax/account onchange logic.
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': original_invoice.partner_id.id,
            'invoice_date': fields.Date.today(),
            'invoice_date_due': fields.Date.today() + timedelta(days=1),
            'invoice_origin': f"Late fee for {original_invoice.name}",
            'company_id': original_invoice.company_id.id,
            # Direct link so dunning + SSLCommerz IPN can resolve the subscription quickly.
            'saas_subscription_id': original_invoice.saas_subscription_id.id or self.subscription_id.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': product.id,
                'name': f"Late fee for overdue invoice {original_invoice.name}",
                'quantity': 1,
                'price_unit': amount,
            })],
        })
        invoice.action_post()
        return invoice

    def _send_dunning_email(self, level):
        """Send dunning email with invoice context (so templates can render the SSLCommerz pay link)."""
        self.ensure_one()

        template_map = {
            'reminder_1': 'saas_billing.email_template_dunning_reminder_1',
            'reminder_2': 'saas_billing.email_template_dunning_reminder_2',
            'final_warning': 'saas_billing.email_template_dunning_final_warning',
            'suspension': 'saas_billing.email_template_dunning_suspension',
        }
        template_xml_id = template_map.get(level)
        if not template_xml_id:
            return
        try:
            template = self.env.ref(template_xml_id)
            # Inject invoice via record context so {{ ctx.get('invoice_id') }} renders.
            template.with_context(invoice_id=self.invoice_id).send_mail(
                self.subscription_id.id, force_send=True,
            )
        except Exception as e:
            _logger.warning(f"Failed to send dunning email: {e}")

    @api.model
    def _cron_cleanup_resolved_dunning(self):
        """Clean up resolved dunning records"""
        _logger.info("Cleaning up resolved dunning records")
        
        # Find subscriptions that have paid their overdue invoices
        resolved_records = self.search([
            ('state', '=', 'resolved'),
            ('last_notification_date', '<', fields.Datetime.now() - timedelta(days=30))
        ])
        
        count = len(resolved_records)
        resolved_records.unlink()
        
        _logger.info(f"Cleaned up {count} resolved dunning records")
        return True