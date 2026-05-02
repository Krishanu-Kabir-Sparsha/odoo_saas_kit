from odoo import models, fields, api, _
from datetime import timedelta
import logging

_logger = logging.getLogger(__name__)

class SaasPointsTransaction(models.Model):
    _name = 'saas.points.transaction'
    _description = 'Points Transaction'
    _order = 'date desc'
    _rec_name = 'display_name'

    partner_id = fields.Many2one('res.partner', string='Customer', required=True, ondelete='cascade')
    subscription_id = fields.Many2one('saas.subscription', string='Subscription', ondelete='set null')
    invoice_id = fields.Many2one('account.move', string='Invoice', ondelete='set null')
    
    points = fields.Integer(string='Points', required=True, help='Positive = earned, Negative = redeemed/expired')
    transaction_type = fields.Selection([
        ('earn', 'Earned'),
        ('redeem', 'Redeemed'),
        ('expire', 'Expired'),
        ('revert', 'Reverted')
    ], string='Transaction Type', required=True)
    
    date = fields.Datetime(string='Date', required=True, default=fields.Datetime.now)
    expiry_date = fields.Date(string='Expiry Date', help='Date when these points expire (for earned points)')
    description = fields.Text(string='Description')
    
    display_name = fields.Char(string='Display Name', compute='_compute_display_name', store=True)
    
    @api.depends('date', 'transaction_type', 'points')
    def _compute_display_name(self):
        for trans in self:
            trans.display_name = f"{trans.date.strftime('%Y-%m-%d')} - {trans.transaction_type}: {trans.points} points"
    
    @api.model
    def earn_points(self, invoice_id):
        """Earn points from a paid invoice"""
        invoice = self.env['account.move'].browse(invoice_id)
        
        if not invoice or invoice.payment_state != 'paid':
            _logger.warning(f"Cannot earn points: Invoice {invoice_id} not paid")
            return False
        
        # Check if points already earned for this invoice
        existing = self.search([('invoice_id', '=', invoice.id), ('transaction_type', '=', 'earn')], limit=1)
        if existing:
            _logger.info(f"Points already earned for invoice {invoice.name}")
            return existing
        
        # Find subscription linked to this invoice
        subscription = self.env['saas.subscription'].search([
            ('sale_order_id', '=', invoice.invoice_origin)
        ], limit=1)
        
        if not subscription:
            _logger.warning(f"No subscription found for invoice {invoice.name}")
            return False
        
        # Calculate points
        config = self.env['saas.points.config'].get_config()
        points_multiplier = config['points_multiplier']
        
        # Exclude tax and late fees from point calculation
        base_amount = invoice.amount_untaxed
        points_earned = int(base_amount * points_multiplier)
        
        if points_earned <= 0:
            return False
        
        # Create transaction
        expiry_date = fields.Date.today() + timedelta(days=config['points_expiry_months'] * 30)
        
        transaction = self.create({
            'partner_id': subscription.partner_id.id,
            'subscription_id': subscription.id,
            'invoice_id': invoice.id,
            'points': points_earned,
            'transaction_type': 'earn',
            'expiry_date': expiry_date,
            'description': f"Earned {points_earned} points from invoice {invoice.name}",
        })
        
        # Update partner points balance
        transaction._update_partner_balance()
        
        # Add message to subscription
        subscription.message_post(
            body=f"Earned {points_earned} loyalty points from invoice {invoice.name}",
            subject="Points Earned"
        )
        
        _logger.info(f"Earned {points_earned} points for partner {subscription.partner_id.name}")
        return transaction
    
    def redeem_points(self, partner_id, points_to_redeem, subscription_id=None, invoice_id=None):
        """Redeem points for discount"""
        # Check if partner has enough points
        partner_points = self.env['saas.partner.points'].search([('partner_id', '=', partner_id)], limit=1)
        
        if not partner_points or partner_points.balance < points_to_redeem:
            raise UserError(_('Insufficient points balance.'))
        
        config = self.env['saas.points.config'].get_config()
        if points_to_redeem < config['min_points_redemption']:
            raise UserError(_(f'Minimum redemption is {config["min_points_redemption"]} points.'))
        
        # Create redemption transaction
        transaction = self.create({
            'partner_id': partner_id,
            'subscription_id': subscription_id,
            'invoice_id': invoice_id,
            'points': -points_to_redeem,
            'transaction_type': 'redeem',
            'description': f"Redeemed {points_to_redeem} points for discount",
        })
        
        # Update partner balance
        transaction._update_partner_balance()
        
        return transaction
    
    def _update_partner_balance(self):
        """Update partner's total points balance"""
        for transaction in self:
            partner_points = self.env['saas.partner.points'].search([
                ('partner_id', '=', transaction.partner_id.id)
            ], limit=1)
            
            if not partner_points:
                partner_points = self.env['saas.partner.points'].create({
                    'partner_id': transaction.partner_id.id,
                    'balance': 0
                })
            
            # Recalculate balance from all transactions
            all_transactions = self.search([
                ('partner_id', '=', transaction.partner_id.id),
                ('transaction_type', 'in', ['earn', 'redeem', 'expire', 'revert'])
            ])
            
            new_balance = sum(all_transactions.mapped('points'))
            partner_points.balance = new_balance
    
    @api.model
    def _cron_expire_points(self):
        """Cron job: Expire points older than expiry date"""
        _logger.info("Running points expiry cron job")
        
        today = fields.Date.today()
        
        # Find expired points that haven't been marked as expired
        expired_transactions = self.search([
            ('transaction_type', '=', 'earn'),
            ('expiry_date', '<', today),
            ('points', '>', 0)
        ])
        
        expired_count = 0
        for transaction in expired_transactions:
            # Create expiry transaction
            self.create({
                'partner_id': transaction.partner_id.id,
                'subscription_id': transaction.subscription_id.id,
                'points': -transaction.points,
                'transaction_type': 'expire',
                'description': f"Points expired from transaction on {transaction.date.strftime('%Y-%m-%d')}",
            })
            
            # Mark original transaction as expired (optional: add field)
            expired_count += transaction.points
        
        # Update balances
        for partner in expired_transactions.mapped('partner_id'):
            self._update_partner_balance_for_partner(partner.id)
        
        _logger.info(f"Expired {expired_count} points across {len(expired_transactions)} transactions")
        return True
    
    def _update_partner_balance_for_partner(self, partner_id):
        """Update balance for a specific partner"""
        partner_points = self.env['saas.partner.points'].search([
            ('partner_id', '=', partner_id)
        ], limit=1)
        
        if partner_points:
            all_transactions = self.search([
                ('partner_id', '=', partner_id),
                ('transaction_type', 'in', ['earn', 'redeem', 'expire', 'revert'])
            ])
            new_balance = sum(all_transactions.mapped('points'))
            partner_points.balance = new_balance