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
    
    def write(self, vals):
        """Detect payment_state changes to earn points when invoice gets paid.
        
        In Odoo 18, _reconcile_paid_amount() no longer exists. The correct
        hook is to watch for payment_state transitions to 'paid' via write().
        """
        result = super(AccountMove, self).write(vals)
        
        if vals.get('payment_state') == 'paid':
            for invoice in self:
                if invoice.move_type == 'out_invoice':
                    try:
                        self.env['saas.points.transaction'].earn_points(invoice.id)
                    except Exception as e:
                        # Non-fatal: don't block payment reconciliation
                        import logging
                        logging.getLogger(__name__).warning(
                            f"Points earning failed for invoice {invoice.id}: {e}"
                        )
        
        return result