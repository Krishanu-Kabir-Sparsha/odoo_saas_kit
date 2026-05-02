from odoo import models, fields, api, _

class SubscriptionChangePackageWizard(models.TransientModel):
    _name = 'subscription.change.package.wizard'
    _description = 'Change Subscription Package Wizard'

    subscription_id = fields.Many2one('saas.subscription', string='Subscription', required=True)
    new_package_id = fields.Many2one('saas.package', string='New Package', required=True)
    effective_date = fields.Date(string='Effective Date', default=fields.Date.today, required=True)
    reason = fields.Text(string='Reason for Change')

    def action_confirm(self):
        """Change the subscription package"""
        self.ensure_one()
        subscription = self.subscription_id
        
        # Log the change
        old_package = subscription.package_id.name
        subscription.write({
            'package_id': self.new_package_id.id,
            'state_reason': f"Package changed from {old_package} to {self.new_package_id.name}. Reason: {self.reason or 'N/A'}"
        })
        
        # Refresh next invoice date based on new package pricing
        subscription._log_state_change(
            subscription.state, 
            subscription.state, 
            f"Package changed: {old_package} → {self.new_package_id.name}"
        )
        
        return {'type': 'ir.actions.act_window_close'}