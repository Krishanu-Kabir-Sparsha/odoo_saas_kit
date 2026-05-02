from odoo import models, fields, api

class ResPartner(models.Model):
    _inherit = 'res.partner'

    points_balance = fields.Integer(string='Loyalty Points', compute='_compute_points_balance', store=False)
    points_ids = fields.One2many('saas.partner.points', 'partner_id', string='Points')
    
    def _compute_points_balance(self):
        for partner in self:
            points_record = self.env['saas.partner.points'].search([('partner_id', '=', partner.id)], limit=1)
            partner.points_balance = points_record.balance if points_record else 0
    
    def action_view_points(self):
        """Open points view for this partner"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'My Points',
            'res_model': 'saas.points.transaction',
            'view_mode': 'tree,form',
            'domain': [('partner_id', '=', self.id)],
            'target': 'current',
        }