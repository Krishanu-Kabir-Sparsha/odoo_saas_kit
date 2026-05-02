from odoo import models, fields, api, _

class SaasLateFeeConfig(models.TransientModel):
    _name = 'saas.late.fee.config'
    _description = 'SaaS Late Fee Configuration'

    late_fee_percent = fields.Float(string='Late Fee Percentage', default=5.0, help='Percentage of invoice amount to charge as late fee')
    dunning_reminder_days = fields.Char(string='Dunning Reminder Days', default='2,5,8', help='Comma-separated days after due to send reminders')
    grace_period_days = fields.Integer(string='Grace Period Days', default=9, help='Days after due before suspension')
    
    @api.model
    def get_config(self):
        """Get current late fee configuration"""
        return {
            'late_fee_percent': float(self.env['ir.config_parameter'].sudo().get_param('saas.late_fee_percent', '5')),
            'dunning_reminder_days': self.env['ir.config_parameter'].sudo().get_param('saas.dunning_reminder_days', '2,5,8'),
            'grace_period_days': int(self.env['ir.config_parameter'].sudo().get_param('saas.grace_period_days', '9')),
        }
    
    def action_save_config(self):
        """Save configuration to system parameters"""
        self.ensure_one()
        self.env['ir.config_parameter'].sudo().set_param('saas.late_fee_percent', str(self.late_fee_percent))
        self.env['ir.config_parameter'].sudo().set_param('saas.dunning_reminder_days', self.dunning_reminder_days)
        self.env['ir.config_parameter'].sudo().set_param('saas.grace_period_days', str(self.grace_period_days))
        
        return {
            'type': 'ir.actions.act_window_close',
        }