from odoo import models, fields, api, _
from odoo.exceptions import UserError

class AccountMove(models.Model):
    _inherit = 'account.move'

    points_earned = fields.Integer(string='Points Earned', compute='_compute_points_earned', store=False)
    points_redeemed = fields.Integer(string='Points Redeemed', compute='_compute_points_redeemed', store=False)
    points_discount_amount = fields.Float(string='Points Discount', compute='_compute_points_discount', store=False)
    saas_subscription_id = fields.Many2one('saas.subscription', string='SaaS Subscription')
    
    def _compute_points_earned(self):
        for invoice in self:
            transactions = self.env['saas.points.transaction'].search([
                ('invoice_id', '=', invoice.id),
                ('transaction_type', '=', 'earn')
            ])
            invoice.points_earned = sum(transactions.mapped('points'))
    
    def _compute_points_redeemed(self):
        for invoice in self:
            transactions = self.env['saas.points.transaction'].search([
                ('invoice_id', '=', invoice.id),
                ('transaction_type', '=', 'redeem')
            ])
            invoice.points_redeemed = abs(sum(transactions.mapped('points')))
    
    def _compute_points_discount(self):
        for invoice in self:
            config = self.env['saas.points.config'].get_config()
            value_per_point = config['points_value_per_unit']
            invoice.points_discount_amount = invoice.points_redeemed * value_per_point
    
    def action_post(self):
        """Override to handle points earning after invoice is posted and paid"""
        result = super(AccountMove, self).action_post()
        
        # If invoice is already paid, earn points immediately
        for invoice in self:
            if invoice.payment_state == 'paid' and invoice.move_type == 'out_invoice':
                self.env['saas.points.transaction'].earn_points(invoice.id)
        
        return result
    
    def _reconcile_paid_amount(self):
        """Override to earn points when payment is reconciled"""
        result = super(AccountMove, self)._reconcile_paid_amount()
        
        for invoice in self:
            if invoice.payment_state == 'paid' and invoice.move_type == 'out_invoice':
                self.env['saas.points.transaction'].earn_points(invoice.id)
        
        return result