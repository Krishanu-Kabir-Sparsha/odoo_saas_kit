from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)


class SaasAdminMixin(models.AbstractModel):
    _name = 'saas.admin.mixin'
    _description = 'SaaS Admin Mixin - Shared admin functionality'

    @api.model
    def get_dashboard_data(self):
        """Get comprehensive dashboard data for the SaaS Command Center."""

        # ─── Subscription Stats ───
        Sub = self.env['saas.subscription']
        total_subs = Sub.search_count([])
        active_subs = Sub.search_count([('state', '=', 'active')])
        pending_subs = Sub.search_count([('state', '=', 'pending')])
        suspended_subs = Sub.search_count([('state', '=', 'suspended')])
        cancelled_subs = Sub.search_count([
            ('state', 'in', ['cancelled', 'force_cancelled'])
        ])
        failed_subs = Sub.search_count([
            ('state', '=', 'provisioning_failed')
        ])

        # ─── Revenue Stats ───
        paid_invoices = self.env['account.move'].search([
            ('move_type', '=', 'out_invoice'),
            ('payment_state', '=', 'paid'),
        ])
        total_revenue = sum(paid_invoices.mapped('amount_total'))

        # Current month revenue
        month_start = fields.Date.today().replace(day=1)
        monthly_invoices = paid_invoices.filtered(
            lambda i: i.invoice_date and i.invoice_date >= month_start
        )
        monthly_revenue = sum(monthly_invoices.mapped('amount_total'))

        # ─── Revenue Trend (last 30 days) ───
        revenue_trend = []
        today = fields.Date.today()
        for i in range(29, -1, -1):
            day = today - timedelta(days=i)
            day_invoices = paid_invoices.filtered(
                lambda inv, d=day: inv.invoice_date == d
            )
            revenue_trend.append({
                'date': day.strftime('%b %d'),
                'amount': sum(day_invoices.mapped('amount_total')),
            })

        # ─── Points Stats ───
        PointsTxn = self.env['saas.points.transaction']
        all_points_txns = PointsTxn.search([])
        total_earned = sum(
            t.points for t in all_points_txns
            if t.transaction_type == 'earn'
        )
        total_redeemed = abs(sum(
            t.points for t in all_points_txns
            if t.transaction_type == 'redeem'
        ))
        total_expired = abs(sum(
            t.points for t in all_points_txns
            if t.transaction_type == 'expire'
        ))
        total_bonus = sum(
            t.points for t in all_points_txns
            if t.transaction_type == 'bonus'
        )

        # Active balance across all partners
        partner_points = self.env['saas.partner.points'].search([])
        active_points_balance = sum(partner_points.mapped('balance'))

        # ─── Package Popularity ───
        packages = self.env['saas.package'].search([])
        package_stats = []
        for pkg in packages:
            pkg_subs = Sub.search_count([
                ('package_id', '=', pkg.id),
                ('state', '=', 'active'),
            ])
            package_stats.append({
                'id': pkg.id,
                'name': pkg.name,
                'count': pkg_subs,
            })
        # Sort by popularity
        package_stats.sort(key=lambda x: x['count'], reverse=True)

        # ─── Recent Activity ───
        recent_activities = []
        try:
            recent_logs = self.env['saas.subscription.log'].search(
                [], order='timestamp desc', limit=15
            )
            for log in recent_logs:
                recent_activities.append({
                    'description': log.description or log.action,
                    'timestamp': log.timestamp.strftime('%b %d, %H:%M')
                                 if log.timestamp else '',
                    'type': log.action or 'info',
                })
        except Exception:
            # Log model may not exist yet
            pass

        # Fallback: recent subscriptions as activity
        if not recent_activities:
            recent_subs = Sub.search(
                [], order='create_date desc', limit=10
            )
            for sub in recent_subs:
                recent_activities.append({
                    'description': (
                        f"{sub.partner_id.name} — "
                        f"{sub.package_id.name} ({sub.state})"
                    ),
                    'timestamp': sub.create_date.strftime('%b %d, %H:%M')
                                 if sub.create_date else '',
                    'type': sub.state,
                })

        # ─── Provisioning Stats ───
        Prov = self.env['tenant.provisioner']
        prov_completed = Prov.search_count([('state', '=', 'completed')])
        prov_failed = Prov.search_count([('state', '=', 'failed')])
        prov_pending = Prov.search_count([
            ('state', 'in', ['pending', 'provisioning'])
        ])

        # ─── System Health (latest) ───
        cpu_usage = 0.0
        memory_usage = 0.0
        disk_usage = 0.0
        try:
            import psutil
            cpu_usage = psutil.cpu_percent(interval=0.5)
            memory_usage = psutil.virtual_memory().percent
            disk_usage = psutil.disk_usage('/').percent
        except (ImportError, Exception):
            pass

        return {
            # Subscription KPIs
            'total_subscriptions': total_subs,
            'active_subscriptions': active_subs,
            'pending_subscriptions': pending_subs,
            'suspended_subscriptions': suspended_subs,
            'cancelled_subscriptions': cancelled_subs,
            'failed_subscriptions': failed_subs,

            # Revenue
            'total_revenue': round(total_revenue, 2),
            'monthly_revenue': round(monthly_revenue, 2),
            'revenue_trend': revenue_trend,

            # Points
            'points_earned': total_earned,
            'points_redeemed': total_redeemed,
            'points_expired': total_expired,
            'points_bonus': total_bonus,
            'points_active_balance': active_points_balance,

            # Packages
            'package_stats': package_stats,

            # Activity
            'recent_activities': recent_activities,

            # Provisioning
            'prov_completed': prov_completed,
            'prov_failed': prov_failed,
            'prov_pending': prov_pending,

            # System Health
            'cpu_usage': round(cpu_usage, 1),
            'memory_usage': round(memory_usage, 1),
            'disk_usage': round(disk_usage, 1),
        }

    # Keep legacy method for backward compatibility
    @api.model
    def get_system_stats(self):
        """Get overall system statistics (legacy)."""
        data = self.get_dashboard_data()
        return {
            'total_subscriptions': data['total_subscriptions'],
            'active_subscriptions': data['active_subscriptions'],
            'suspended_subscriptions': data['suspended_subscriptions'],
            'pending_subscriptions': data['pending_subscriptions'],
            'failed_provisioning': data['failed_subscriptions'],
            'provisioning_failures': data['prov_failed'],
            'total_revenue': data['total_revenue'],
            'monthly_revenue': data['monthly_revenue'],
            'total_points': data['points_active_balance'],
        }

    @api.model
    def get_recent_activity(self, limit=10):
        """Get recent subscription activity."""
        try:
            recent_logs = self.env['saas.subscription.log'].search(
                [], order='timestamp desc', limit=limit
            )
            return recent_logs
        except Exception:
            return []

    def action_force_provision(self, subscription_ids):
        """Force provision tenants for selected subscriptions."""
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
                        subject="Force Provisioning Triggered",
                    )
                except Exception as e:
                    fail_count += 1
                    _logger.error(
                        f"Force provision failed for {sub.name}: {e}")

        return {'success': success_count, 'failed': fail_count}

    def action_retry_failed_invoices(self, invoice_ids):
        """Retry failed invoice generation."""
        invoices = self.env['account.move'].browse(invoice_ids)
        success_count = 0

        for invoice in invoices:
            if invoice.payment_state != 'paid':
                try:
                    subscription = self.env['saas.subscription'].search([
                        ('sale_order_id.name', '=', invoice.invoice_origin)
                    ], limit=1)

                    if subscription:
                        subscription.message_post(
                            body=(
                                f"Payment reminder sent for "
                                f"invoice {invoice.name}"
                            ),
                            subject="Payment Reminder",
                        )
                    success_count += 1
                except Exception as e:
                    _logger.error(
                        f"Retry failed for invoice {invoice.name}: {e}")

        return success_count

    def action_manual_refund(self, invoice_id, refund_reason):
        """Process manual refund for invoice."""
        invoice = self.env['account.move'].browse(invoice_id)

        if invoice.payment_state != 'paid':
            raise UserError(_('Only paid invoices can be refunded.'))

        try:
            refund = invoice._reverse_moves(default_values={
                'ref': f"Refund: {refund_reason[:50]}",
            })

            refund.action_post()

            subscription = self.env['saas.subscription'].search([
                ('sale_order_id.name', '=', invoice.invoice_origin)
            ], limit=1)

            if subscription:
                subscription.message_post(
                    body=(
                        f"Manual refund processed for invoice "
                        f"{invoice.name}<br/>Reason: {refund_reason}"
                    ),
                    subject="Refund Processed",
                )

            return refund

        except Exception as e:
            raise UserError(_(f'Refund failed: {str(e)}'))