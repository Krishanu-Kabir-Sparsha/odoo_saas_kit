from odoo import models, fields, api, _

class SaasPartnerPoints(models.Model):
    _name = 'saas.partner.points'
    _description = 'Partner Points Balance'
    _rec_name = 'partner_id'

    partner_id = fields.Many2one('res.partner', string='Customer', required=True, ondelete='cascade')
    balance = fields.Integer(string='Current Points Balance', default=0, readonly=True)
    total_earned = fields.Integer(string='Total Points Earned', compute='_compute_totals', store=False)
    total_redeemed = fields.Integer(string='Total Points Redeemed', compute='_compute_totals', store=False)
    last_updated = fields.Datetime(string='Last Updated', default=fields.Datetime.now)
    
    @api.depends('partner_id')
    def _compute_totals(self):
        for record in self:
            transactions = self.env['saas.points.transaction'].search([
                ('partner_id', '=', record.partner_id.id)
            ])
            record.total_earned = sum(t.points for t in transactions if t.points > 0)
            record.total_redeemed = abs(sum(t.points for t in transactions if t.points < 0))
    
    def action_view_transactions(self):
        """View all transactions for this partner"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Points Transactions',
            'res_model': 'saas.points.transaction',
            'view_mode': 'tree,form',
            'domain': [('partner_id', '=', self.partner_id.id)],
            'target': 'current',
        }
    
    def calculate_discount_value(self, points):
        """Calculate monetary value of points"""
        config = self.env['saas.points.config'].get_config()
        value_per_point = config['points_value_per_unit']
        return points * value_per_point