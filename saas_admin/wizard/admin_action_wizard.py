from odoo import models, fields, api, _
from odoo.exceptions import UserError
import subprocess
import logging

_logger = logging.getLogger(__name__)


class AdminForceActionWizard(models.TransientModel):
    _name = 'admin.force.action.wizard'
    _description = 'Admin Force Action Wizard'

    subscription_ids = fields.Many2many('saas.subscription', string='Subscriptions', required=True)
    action_type = fields.Selection([
        ('activate', 'Force Activate'),
        ('suspend', 'Force Suspend'),
        ('cancel', 'Force Cancel'),
        ('retry_provision', 'Retry Provisioning'),
    ], string='Action', required=True)
    reason = fields.Text(string='Reason', required=True)

    def action_execute(self):
        """Execute the selected force action"""
        self.ensure_one()
        subscriptions = self.subscription_ids

        if self.action_type == 'activate':
            subscriptions.admin_force_activate()
        elif self.action_type == 'suspend':
            subscriptions.admin_force_suspend()
        elif self.action_type == 'cancel':
            subscriptions.admin_force_cancel()
        elif self.action_type == 'retry_provision':
            subscriptions.admin_retry_provisioning()

        # Log the bulk action
        for sub in subscriptions:
            sub.message_post(
                body=f"Bulk admin action: {self.action_type}<br/>Reason: {self.reason}",
                subject="Admin Bulk Action"
            )

        return {'type': 'ir.actions.act_window_close'}


class AdminTenantDeleteWizard(models.TransientModel):
    _name = 'admin.tenant.delete.wizard'
    _description = 'Admin Tenant Delete Wizard'

    subscription_id = fields.Many2one('saas.subscription', string='Subscription', required=True)
    tenant_db_name = fields.Char(string='Database Name', readonly=True)
    confirm_delete = fields.Boolean(string='Confirm Permanent Deletion', required=True)
    reason = fields.Text(string='Reason for Deletion', required=True)

    def action_delete_tenant(self):
        """Permanently delete tenant database"""
        self.ensure_one()

        if not self.confirm_delete:
            raise UserError(_('Please confirm that you want to permanently delete this tenant.'))

        subscription = self.subscription_id
        db_name = subscription.tenant_db_name

        if not db_name:
            raise UserError(_('No tenant database found for this subscription.'))

        try:
            # Drop database
            cmd = f"dropdb --if-exists {db_name}"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

            if result.returncode != 0:
                raise Exception(result.stderr)

            # Clear tenant info from subscription
            subscription.write({
                'tenant_db_name': False,
                'tenant_db_password': False,
                'tenant_url': False,
                'state_reason': f"Tenant manually deleted by admin. Reason: {self.reason}"
            })

            subscription.message_post(
                body=f"Tenant database '{db_name}' permanently deleted.<br/>Reason: {self.reason}",
                subject="Tenant Deleted"
            )

            _logger.info(f"Tenant database {db_name} deleted by admin for subscription {subscription.name}")

        except Exception as e:
            raise UserError(_(f'Failed to delete tenant database: {str(e)}'))

        return {'type': 'ir.actions.act_window_close'}