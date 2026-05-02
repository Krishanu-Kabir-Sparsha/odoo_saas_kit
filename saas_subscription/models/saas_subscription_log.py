from odoo import models, fields, api

class SaasSubscriptionLog(models.Model):
    _name = 'saas.subscription.log'
    _description = 'Subscription State Change Log'
    _order = 'timestamp desc'
    _rec_name = 'display_name'

    subscription_id = fields.Many2one('saas.subscription', string='Subscription', required=True, ondelete='cascade')
    user_id = fields.Many2one('res.users', string='User', required=True, default=lambda self: self.env.user)
    
    from_state = fields.Selection([
        ('draft', 'Draft'),
        ('pending', 'Pending Payment'),
        ('active', 'Active'),
        ('suspended', 'Suspended'),
        ('canceled', 'Canceled'),
        ('rejected', 'Rejected'),
        ('provisioning_failed', 'Provisioning Failed')
    ], string='From State')
    
    to_state = fields.Selection([
        ('draft', 'Draft'),
        ('pending', 'Pending Payment'),
        ('active', 'Active'),
        ('suspended', 'Suspended'),
        ('canceled', 'Canceled'),
        ('rejected', 'Rejected'),
        ('provisioning_failed', 'Provisioning Failed')
    ], string='To State', required=True)
    
    reason = fields.Text(string='Reason')
    timestamp = fields.Datetime(string='Timestamp', default=fields.Datetime.now, required=True)
    
    display_name = fields.Char(string='Display Name', compute='_compute_display_name', store=True)
    
    @api.depends('timestamp', 'subscription_id', 'from_state', 'to_state')
    def _compute_display_name(self):
        for log in self:
            log.display_name = f"{log.timestamp} - {log.subscription_id.name}: {log.from_state} → {log.to_state}"