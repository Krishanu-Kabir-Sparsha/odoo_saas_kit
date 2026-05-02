from odoo import models, fields, api, _
from odoo.exceptions import UserError

class PointsRedeemWizard(models.TransientModel):
    _name = 'points.redeem.wizard'
    _description = 'Points Redemption Wizard'

    partner_id = fields.Many2one('res.partner', string='Customer', required=True)
    available_points = fields.Integer(string='Available Points', compute='_compute_available_points')
    points_to_redeem = fields.Integer(string='Points to Redeem', required=True)
    discount_value = fields.Float(string='Discount Value', compute='_compute_discount_value')
    subscription_id = fields.Many2one('saas.subscription', string='Apply to Subscription')
    
    @api.depends('partner_id')
    def _compute_available_points(self):
        for wizard in self:
            points_record = self.env['saas.partner.points'].search([
                ('partner_id', '=', wizard.partner_id.id)
            ], limit=1)
            wizard.available_points = points_record.balance if points_record else 0
    
    @api.depends('points_to_redeem')
    def _compute_discount_value(self):
        for wizard in self:
            config = self.env['saas.points.config'].get_config()
            wizard.discount_value = wizard.points_to_redeem * config['points_value_per_unit']
    
    @api.constrains('points_to_redeem')
    def _check_points(self):
        for wizard in self:
            if wizard.points_to_redeem > wizard.available_points:
                raise UserError(_('Cannot redeem more points than available.'))
            
            config = self.env['saas.points.config'].get_config()
            if wizard.points_to_redeem < config['min_points_redemption']:
                raise UserError(_(f'Minimum redemption is {config["min_points_redemption"]} points.'))
    
    def action_redeem(self):
        """Redeem points and apply to subscription"""
        self.ensure_one()
        
        transaction = self.env['saas.points.transaction'].redeem_points(
            self.partner_id.id,
            self.points_to_redeem,
            subscription_id=self.subscription_id.id if self.subscription_id else None
        )
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'saas.points.transaction',
            'res_id': transaction.id,
            'view_mode': 'form',
            'target': 'current',
        }