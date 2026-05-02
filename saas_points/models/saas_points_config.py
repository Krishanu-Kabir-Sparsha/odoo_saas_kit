from odoo import models, fields, api, _

class SaasPointsConfig(models.TransientModel):
    _name = 'saas.points.config'
    _description = 'SaaS Points System Configuration'

    points_multiplier = fields.Float(string='Points per Currency Unit', default=1.0, 
                                      help='Number of points earned per 1 unit of currency')
    points_expiry_months = fields.Integer(string='Points Expiry (Months)', default=12,
                                           help='Number of months after which points expire')
    min_points_redemption = fields.Integer(string='Minimum Points to Redeem', default=100,
                                            help='Minimum points required for redemption')
    points_value_per_unit = fields.Float(string='Points Value per Unit', default=0.01,
                                          help='Monetary value of 1 point (e.g., 0.01 = $0.01 per point)')
    max_discount_percent = fields.Float(string='Maximum Discount Percentage', default=50,
                                         help='Maximum discount from points as % of invoice total')
    
    @api.model
    def get_config(self):
        """Get current points configuration"""
        return {
            'points_multiplier': float(self.env['ir.config_parameter'].sudo().get_param('saas.points.multiplier', '1.0')),
            'points_expiry_months': int(self.env['ir.config_parameter'].sudo().get_param('saas.points.expiry_months', '12')),
            'min_points_redemption': int(self.env['ir.config_parameter'].sudo().get_param('saas.points.min_redemption', '100')),
            'points_value_per_unit': float(self.env['ir.config_parameter'].sudo().get_param('saas.points.value_per_unit', '0.01')),
            'max_discount_percent': float(self.env['ir.config_parameter'].sudo().get_param('saas.points.max_discount_percent', '50')),
        }
    
    def action_save_config(self):
        """Save configuration to system parameters"""
        self.ensure_one()
        self.env['ir.config_parameter'].sudo().set_param('saas.points.multiplier', str(self.points_multiplier))
        self.env['ir.config_parameter'].sudo().set_param('saas.points.expiry_months', str(self.points_expiry_months))
        self.env['ir.config_parameter'].sudo().set_param('saas.points.min_redemption', str(self.min_points_redemption))
        self.env['ir.config_parameter'].sudo().set_param('saas.points.value_per_unit', str(self.points_value_per_unit))
        self.env['ir.config_parameter'].sudo().set_param('saas.points.max_discount_percent', str(self.max_discount_percent))
        
        return {
            'type': 'ir.actions.act_window_close',
        }