from odoo import models, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class SaasAdminMixin(models.AbstractModel):
    _name = 'saas.admin.mixin'
    _description = 'SaaS Admin Mixin - Shared admin functionality'

    @api.model
    def get_system_stats(self):
        """Get overall system statistics"""
        # Subscription counts
        total_subs = self.env['saas.subscription'].search_count([])
        active_subs = self.env['saas.subscription'].search_count([('state', '=', 'active')])
        suspended_subs = self.env['saas.subscription'].search_count([('state', '=', 'suspended')])
        pending_subs = self.env['saas.subscription'].search_count([('state', '=', 'pending')])
        failed_subs = self.env['saas.subscription'].search_count([('state', '=', 'provisioning_failed')])

        # Revenue stats (from paid invoices)
        paid_invoices = self.env['account.move'].search([
            ('move_type', '=', 'out_invoice'),
            ('payment_state', '=', 'paid')
        ])
        total_revenue = sum(paid_invoices.mapped('amount_total'))
        monthly_revenue = sum(paid_invoices.filtered(
            lambda i: i.invoice_date >= fields.Date.today().replace(day=1)
        ).mapped('amount_total'))

        # Points stats
        total_points = sum(self.env['saas.partner.points'].search([]).mapped('balance'))

        # Provisioning stats
        failed_provisioning = self.env['tenant.provisioner'].search_count([('state', '=', 'failed')])

        return {
            'total_subscriptions': total_subs,
            'active_subscriptions': active_subs,
            'suspended_subscriptions': suspended_subs,
            'pending_subscriptions': pending_subs,
            'failed_provisioning': failed_subs,
            'provisioning_failures': failed_provisioning,
            'total_revenue': total_revenue,
            'monthly_revenue': monthly_revenue,
            'total_points': total_points,
        }

    @api.model
    def get_recent_activity(self, limit=10):
        """Get recent subscription activity"""
        recent_logs = self.env['saas.subscription.log'].search(
            [], order='timestamp desc', limit=limit
        )
        return recent_logs

    def action_force_provision(self, subscription_ids):
        """Force provision tenants for selected subscriptions"""
        subscriptions = self.env['saas.subscription'].browse(subscription_ids)
        success_count = 0
        fail_count = 0

        for sub in subscriptions:
            if sub.state in ['pending', 'provisioning_failed']:
                try:
                    sub.write({'state': 'active'})
                    success_count += 1
                    sub.message_post(
                        body="Admin forced provisioning",
                        subject="Force Provisioning Triggered"
                    )
                except Exception as e:
                    fail_count += 1
                    _logger.error(f"Force provision failed for {sub.name}: {e}")

        return {
            'success': success_count,
            'failed': fail_count
        }

    def action_retry_failed_invoices(self, invoice_ids):
        """Retry failed invoice generation"""
        invoices = self.env['account.move'].browse(invoice_ids)
        success_count = 0

        for invoice in invoices:
            if invoice.payment_state != 'paid':
                try:
                    # Find subscription
                    subscription = self.env['saas.subscription'].search([
                        ('sale_order_id.name', '=', invoice.invoice_origin)
                    ], limit=1)

                    if subscription:
                        # Send reminder
                        subscription.message_post(
                            body=f"Payment reminder sent for invoice {invoice.name}",
                            subject="Payment Reminder"
                        )
                    success_count += 1
                except Exception as e:
                    _logger.error(f"Retry failed for invoice {invoice.name}: {e}")

        return success_count

    def action_manual_refund(self, invoice_id, refund_reason):
        """Process manual refund for invoice"""
        invoice = self.env['account.move'].browse(invoice_id)

        if invoice.payment_state != 'paid':
            raise UserError(_('Only paid invoices can be refunded.'))

        try:
            # Create credit note
            refund = invoice._reverse_moves(default_values={
                'ref': f"Refund: {refund_reason[:50]}",
            })

            refund.action_post()

            # Find subscription
            subscription = self.env['saas.subscription'].search([
                ('sale_order_id.name', '=', invoice.invoice_origin)
            ], limit=1)

            if subscription:
                subscription.message_post(
                    body=f"Manual refund processed for invoice {invoice.name}<br/>Reason: {refund_reason}",
                    subject="Refund Processed"
                )

            return refund

        except Exception as e:
            raise UserError(_(f'Refund failed: {str(e)}'))