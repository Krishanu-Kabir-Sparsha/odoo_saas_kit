from odoo import models, fields, api, _
from odoo.exceptions import UserError


class SaasSubscriptionAdmin(models.Model):
    _inherit = 'saas.subscription'

    # Admin actions
    def admin_force_activate(self):
        """Admin: Force activate subscription"""
        for record in self:
            if record.state in ['pending', 'suspended', 'provisioning_failed']:
                record.write({'state': 'active'})
                record._log_state_change(record.state, 'active', 'Admin forced activation')
                record.message_post(
                    body="Subscription force activated by admin",
                    subject="Admin Action"
                )
            else:
                raise UserError(_(f'Cannot activate from state: {record.state}'))

    def admin_force_suspend(self):
        """Admin: Force suspend subscription"""
        for record in self:
            if record.state == 'active':
                record.write({'state': 'suspended', 'state_reason': 'Force suspended by admin'})
                record._log_state_change('active', 'suspended', 'Admin forced suspension')
                record.message_post(body="Subscription force suspended by admin", subject="Admin Action")

    def admin_force_cancel(self):
        """Admin: Force cancel subscription"""
        for record in self:
            if record.state not in ['canceled', 'rejected']:
                record.write({'state': 'canceled', 'state_reason': 'Force canceled by admin'})
                record._log_state_change(record.state, 'canceled', 'Admin forced cancellation')
                record.message_post(body="Subscription force canceled by admin", subject="Admin Action")

    def admin_retry_provisioning(self):
        """Admin: Retry failed provisioning"""
        for record in self:
            if record.state == 'provisioning_failed':
                record.write({
                    'state': 'pending',
                    'provision_attempts': record.provision_attempts + 1,
                    'state_reason': False
                })
                record._log_state_change('provisioning_failed', 'pending', 'Admin retry triggered')
                record.message_post(body="Provisioning retry triggered by admin", subject="Admin Action")

    def admin_force_delete_tenant(self):
        """Admin: Immediately delete tenant database"""
        self.ensure_one()
        if self.tenant_db_name:
            from ..wizard import admin_action_wizard
            return {
                'type': 'ir.actions.act_window',
                'name': 'Confirm Tenant Deletion',
                'res_model': 'admin.tenant.delete.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {
                    'default_subscription_id': self.id,
                    'default_tenant_db_name': self.tenant_db_name,
                }
            }