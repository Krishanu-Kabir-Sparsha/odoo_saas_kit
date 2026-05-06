from odoo import models, fields, api, _
from datetime import timedelta
import logging

_logger = logging.getLogger(__name__)

class SaasInvoiceScheduler(models.Model):
    _name = 'saas.invoice.scheduler'
    _description = 'SaaS Invoice Scheduler'
    _inherit = 'saas.billing.mixin'
    _rec_name = 'display_name'

    subscription_id = fields.Many2one('saas.subscription', string='Subscription', required=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed')
    ], string='Status', default='draft')
    invoice_id = fields.Many2one('account.move', string='Generated Invoice', readonly=True)
    invoice_date = fields.Date(string='Invoice Date', readonly=True)
    amount = fields.Float(string='Invoice Amount', readonly=True)
    error_message = fields.Text(string='Error Message')
    processed_at = fields.Datetime(string='Processed At')
    display_name = fields.Char(string='Display Name', compute='_compute_display_name')

    @api.depends('subscription_id', 'invoice_date', 'amount')
    def _compute_display_name(self):
        for record in self:
            record.display_name = f"Invoice for {record.subscription_id.name} - {record.invoice_date or 'Pending'}"

    @api.model
    def _cron_generate_recurring_invoices(self):
        """Cron job: Generate invoices for all active subscriptions with due date <= today"""
        _logger.info("Starting recurring invoice generation cron job")
        
        today = fields.Date.today()
        
        # Find subscriptions that need invoicing
        subscriptions = self.env['saas.subscription'].search([
            ('state', '=', 'active'),
            ('date_next_invoice', '<=', today),
            ('date_end', '=', False)
        ])
        
        _logger.info(f"Found {len(subscriptions)} subscriptions needing invoicing")
        
        success_count = 0
        fail_count = 0
        
        for subscription in subscriptions:
            try:
                # Check if already scheduled
                existing = self.search([
                    ('subscription_id', '=', subscription.id),
                    ('state', 'in', ['draft', 'processing'])
                ], limit=1)
                
                if existing:
                    _logger.info(f"Invoice already scheduled for {subscription.name}")
                    continue
                
                # Create scheduler record
                scheduler = self.create({
                    'subscription_id': subscription.id,
                    'state': 'processing',
                    'invoice_date': today,
                })
                
                # Generate invoice
                invoice = scheduler._generate_invoice()
                
                # Post invoice
                invoice.action_post()
                
                # Update scheduler
                scheduler.write({
                    'state': 'completed',
                    'invoice_id': invoice.id,
                    'amount': invoice.amount_total,
                    'processed_at': fields.Datetime.now()
                })
                
                # Update subscription next invoice date
                self._update_next_invoice_date(subscription)
                
                # Send invoice email
                self._send_invoice_email(subscription, invoice)
                
                success_count += 1
                _logger.info(f"Generated invoice {invoice.name} for {subscription.name}")
                
            except Exception as e:
                fail_count += 1
                _logger.error(f"Failed to generate invoice for {subscription.name}: {str(e)}")
                
                # Create failed entry if not exists
                scheduler = self.search([('subscription_id', '=', subscription.id), ('state', '=', 'processing')], limit=1)
                if scheduler:
                    scheduler.write({
                        'state': 'failed',
                        'error_message': str(e)[:500],
                        'processed_at': fields.Datetime.now()
                    })
                
                # Mark subscription as having billing issue
                subscription.message_post(
                    body=f"Failed to generate recurring invoice: {str(e)[:200]}",
                    subject="Billing Error"
                )
        
        _logger.info(f"Invoice generation completed: {success_count} success, {fail_count} failures")
        return True

    def _generate_invoice(self):
        """Generate invoice for the subscription"""
        self.ensure_one()
        subscription = self.subscription_id
        
        # Get billing amount
        if subscription.billing_cycle == 'yearly':
            amount = subscription.package_id.yearly_price
        else:
            amount = subscription.package_id.monthly_price
        
        # Create sale order if needed
        if not subscription.sale_order_id:
            sale_order = self._create_sale_order(subscription)
            subscription.sale_order_id = sale_order.id
        else:
            sale_order = subscription.sale_order_id
        
        # Create invoice
        invoice = self._create_invoice_from_sale_order(sale_order, amount)
        
        # Link invoice to subscription
        invoice.write({
            'saas_subscription_id': subscription.id,
            'invoice_origin': f"{subscription.name} - Renewal",
        })
        
        return invoice

    def _update_next_invoice_date(self, subscription):
        """Update the next invoice date on subscription"""
        if subscription.billing_cycle == 'yearly':
            subscription.date_next_invoice = fields.Date.today() + timedelta(days=365)
        else:
            subscription.date_next_invoice = fields.Date.today() + timedelta(days=30)
        
        _logger.info(f"Updated next invoice date for {subscription.name} to {subscription.date_next_invoice}")

    def _send_invoice_email(self, subscription, invoice):
        """Send invoice email with the invoice record (templates rely on record fields)."""
        try:
            template = self.env.ref('saas_billing.email_template_invoice_generated', False)
            if template:
                template.with_context(invoice_id=invoice).send_mail(
                    subscription.id, force_send=True,
                )
        except Exception as e:
            _logger.warning(f"Failed to send invoice email: {e}")

    @api.model
    def _cron_sync_missing_invoices(self):
        """Sync any missing invoices that should have been generated"""
        _logger.info("Running missing invoice sync")
        
        today = fields.Date.today()
        
        # Find subscriptions where next invoice date is in the past but no invoice scheduled
        subscriptions = self.env['saas.subscription'].search([
            ('state', '=', 'active'),
            ('date_next_invoice', '<', today),
            ('date_next_invoice', '>', today - timedelta(days=30))  # Only last 30 days
        ])
        
        for subscription in subscriptions:
            # Check if invoice already exists for this period
            existing_invoice = self.search([
                ('subscription_id', '=', subscription.id),
                ('invoice_date', '>=', subscription.date_next_invoice),
                ('invoice_date', '<=', today)
            ], limit=1)
            
            if not existing_invoice:
                _logger.info(f"Missing invoice detected for {subscription.name}, generating now")
                self._cron_generate_recurring_invoices()