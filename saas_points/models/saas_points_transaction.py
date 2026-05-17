from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import timedelta
import logging

_logger = logging.getLogger(__name__)


class SaasPointsTransaction(models.Model):
    _name = 'saas.points.transaction'
    _description = 'Points Transaction'
    _order = 'date desc'
    _rec_name = 'display_name'

    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True, ondelete='cascade')
    subscription_id = fields.Many2one(
        'saas.subscription', string='Subscription', ondelete='set null')
    invoice_id = fields.Many2one(
        'account.move', string='Invoice', ondelete='set null')

    points = fields.Integer(
        string='Points', required=True,
        help='Positive = earned, Negative = redeemed/expired')
    transaction_type = fields.Selection([
        ('earn', 'Earned'),
        ('redeem', 'Redeemed'),
        ('expire', 'Expired'),
        ('revert', 'Reverted'),
        ('bonus', 'Bonus'),
    ], string='Transaction Type', required=True)

    date = fields.Datetime(
        string='Date', required=True, default=fields.Datetime.now)
    expiry_date = fields.Date(
        string='Expiry Date',
        help='Date when these points expire (for earned points)')
    is_expired = fields.Boolean(
        string='Expired', default=False,
        help='Marked True once an expiry transaction has been created '
             'for this earn record. Prevents duplicate expiry entries.')
    description = fields.Text(string='Description')

    display_name = fields.Char(
        string='Display Name', compute='_compute_display_name', store=True)

    @api.depends('date', 'transaction_type', 'points')
    def _compute_display_name(self):
        for trans in self:
            date_str = trans.date.strftime('%Y-%m-%d') if trans.date else '—'
            trans.display_name = (
                f"{date_str} - {trans.transaction_type}: {trans.points} points"
            )

    # ==================== EARN POINTS ====================

    @api.model
    def earn_points(self, invoice_id):
        """Earn points from a paid invoice."""
        invoice = self.env['account.move'].browse(invoice_id)

        if not invoice or invoice.payment_state != 'paid':
            _logger.warning(
                f"Cannot earn points: Invoice {invoice_id} not paid")
            return False

        # Prevent duplicate earning for same invoice
        existing = self.search([
            ('invoice_id', '=', invoice.id),
            ('transaction_type', '=', 'earn'),
        ], limit=1)
        if existing:
            _logger.info(
                f"Points already earned for invoice {invoice.name}")
            return existing

        # Find subscription linked to this invoice
        subscription = self._find_subscription_for_invoice(invoice)
        if not subscription:
            _logger.warning(
                f"No subscription found for invoice {invoice.name}")
            return False

        # Calculate points
        config = self.env['saas.points.config'].get_config()
        points_multiplier = config['points_multiplier']

        # Exclude tax and late fees from point calculation
        base_amount = invoice.amount_untaxed
        points_earned = int(base_amount * points_multiplier)

        if points_earned <= 0:
            return False

        # Calculate expiry date
        expiry_months = config.get('points_expiry_months', 12)
        expiry_date = (
            fields.Date.today() + timedelta(days=expiry_months * 30)
        )

        # Create transaction
        transaction = self.create({
            'partner_id': subscription.partner_id.id,
            'subscription_id': subscription.id,
            'invoice_id': invoice.id,
            'points': points_earned,
            'transaction_type': 'earn',
            'expiry_date': expiry_date,
            'description': (
                f"Earned {points_earned} points from "
                f"invoice {invoice.name}"
            ),
        })

        # Update partner points balance
        transaction._update_partner_balance()

        # Add message to subscription
        subscription.message_post(
            body=(
                f"Earned {points_earned} loyalty points from "
                f"invoice {invoice.name}"
            ),
            subject="Points Earned",
        )

        _logger.info(
            f"Earned {points_earned} points for "
            f"partner {subscription.partner_id.name}"
        )
        return transaction

    @api.model
    def earn_points_on_payment(self, subscription, amount):
        """Earn points when a payment is confirmed (called from payment flow).

        This is the bridge between the payment success handler and the
        loyalty points system. Unlike earn_points() which requires a paid
        invoice, this works directly with the subscription + amount.
        """
        if not subscription or amount <= 0:
            return False

        # Prevent duplicate: check if points were already earned for this
        # subscription in the last 5 minutes (same payment confirmation)
        recent_earn = self.search([
            ('subscription_id', '=', subscription.id),
            ('transaction_type', '=', 'earn'),
            ('date', '>=', fields.Datetime.now() - timedelta(minutes=5)),
        ], limit=1)
        if recent_earn:
            _logger.info(
                f"Points recently earned for {subscription.name}, skipping")
            return recent_earn

        config = self.env['saas.points.config'].get_config()
        points_multiplier = config['points_multiplier']
        points_earned = int(amount * points_multiplier)

        if points_earned <= 0:
            return False

        expiry_months = config.get('points_expiry_months', 12)
        expiry_date = (
            fields.Date.today() + timedelta(days=expiry_months * 30)
        )

        transaction = self.create({
            'partner_id': subscription.partner_id.id,
            'subscription_id': subscription.id,
            'points': points_earned,
            'transaction_type': 'earn',
            'expiry_date': expiry_date,
            'description': (
                f"Earned {points_earned} points from payment of "
                f"৳{amount:.2f} for {subscription.package_id.name}"
            ),
        })

        transaction._update_partner_balance()

        subscription.message_post(
            body=(
                f"🌟 Earned {points_earned} loyalty points from payment "
                f"of ৳{amount:.2f}"
            ),
            subject="Loyalty Points Earned",
        )

        _logger.info(
            f"Earned {points_earned} points for "
            f"{subscription.partner_id.name} on payment of ৳{amount}"
        )
        return transaction

    # ==================== REDEEM POINTS ====================

    def redeem_points(self, partner_id, points_to_redeem,
                      subscription_id=None, invoice_id=None,
                      order_total=None):
        """Redeem points for discount with proper validation."""
        partner_points = self.env['saas.partner.points'].search([
            ('partner_id', '=', partner_id)
        ], limit=1)

        if not partner_points or partner_points.balance < points_to_redeem:
            raise UserError(_('Insufficient points balance.'))

        config = self.env['saas.points.config'].get_config()

        # Validate minimum redemption
        min_redeem = config.get('min_points_redemption', 100)
        if points_to_redeem < min_redeem:
            raise UserError(_(
                f'Minimum redemption is {min_redeem} points.'
            ))

        # Validate max discount percent if order total is provided
        if order_total and order_total > 0:
            max_discount_pct = config.get('max_discount_percent', 50.0)
            value_per_point = config.get('points_value_per_unit', 1.0)
            discount_amount = points_to_redeem * value_per_point
            max_discount = order_total * (max_discount_pct / 100.0)

            if discount_amount > max_discount:
                # Cap points to the max allowed
                max_points = int(max_discount / value_per_point)
                raise UserError(_(
                    f'Maximum discount is {max_discount_pct}% of the '
                    f'order total (৳{max_discount:.2f}). '
                    f'You can redeem up to {max_points} points.'
                ))

        # Create redemption transaction
        transaction = self.create({
            'partner_id': partner_id,
            'subscription_id': subscription_id,
            'invoice_id': invoice_id,
            'points': -points_to_redeem,
            'transaction_type': 'redeem',
            'description': (
                f"Redeemed {points_to_redeem} points for discount"
            ),
        })

        # Update partner balance
        transaction._update_partner_balance()
        return transaction

    # ==================== BALANCE UPDATE ====================

    def _update_partner_balance(self):
        """Update partner's total points balance."""
        for transaction in self:
            partner_points = self.env['saas.partner.points'].search([
                ('partner_id', '=', transaction.partner_id.id)
            ], limit=1)

            if not partner_points:
                partner_points = self.env['saas.partner.points'].create({
                    'partner_id': transaction.partner_id.id,
                    'balance': 0,
                })

            # Recalculate balance from all transactions
            all_transactions = self.search([
                ('partner_id', '=', transaction.partner_id.id),
            ])

            new_balance = sum(all_transactions.mapped('points'))
            partner_points.write({
                'balance': new_balance,
                'last_updated': fields.Datetime.now(),
            })

    def _update_partner_balance_for_partner(self, partner_id):
        """Update balance for a specific partner."""
        partner_points = self.env['saas.partner.points'].search([
            ('partner_id', '=', partner_id)
        ], limit=1)

        if partner_points:
            all_transactions = self.search([
                ('partner_id', '=', partner_id),
            ])
            new_balance = sum(all_transactions.mapped('points'))
            partner_points.write({
                'balance': new_balance,
                'last_updated': fields.Datetime.now(),
            })

    # ==================== CRON: EXPIRE POINTS ====================

    @api.model
    def _cron_expire_points(self):
        """Cron job: Expire points older than expiry date.

        FIXED: Uses `is_expired` flag to prevent creating duplicate
        expiry transactions on every cron run.
        """
        _logger.info("Running points expiry cron job")

        today = fields.Date.today()

        # Find earned points that have expired and haven't been processed
        expired_transactions = self.search([
            ('transaction_type', '=', 'earn'),
            ('expiry_date', '<', today),
            ('points', '>', 0),
            ('is_expired', '=', False),
        ])

        expired_count = 0
        affected_partners = set()

        for transaction in expired_transactions:
            # Create expiry transaction (negative points)
            self.create({
                'partner_id': transaction.partner_id.id,
                'subscription_id': (
                    transaction.subscription_id.id
                    if transaction.subscription_id else False
                ),
                'points': -transaction.points,
                'transaction_type': 'expire',
                'description': (
                    f"Points expired from transaction on "
                    f"{transaction.date.strftime('%Y-%m-%d')}"
                ),
            })

            # Mark original as expired to prevent duplicates
            transaction.write({'is_expired': True})
            expired_count += transaction.points
            affected_partners.add(transaction.partner_id.id)

        # Update balances for all affected partners
        for partner_id in affected_partners:
            self._update_partner_balance_for_partner(partner_id)

        _logger.info(
            f"Expired {expired_count} points across "
            f"{len(expired_transactions)} transactions"
        )
        return True

    # ==================== HELPERS ====================

    def _find_subscription_for_invoice(self, invoice):
        """Find the subscription linked to an invoice."""
        # Direct link
        if hasattr(invoice, 'saas_subscription_id') and \
                invoice.saas_subscription_id:
            return invoice.saas_subscription_id

        # Via sale order
        if invoice.invoice_origin:
            sale_order = self.env['sale.order'].search([
                ('name', '=', invoice.invoice_origin)
            ], limit=1)
            if sale_order:
                subscription = self.env['saas.subscription'].search([
                    ('sale_order_id', '=', sale_order.id)
                ], limit=1)
                if subscription:
                    return subscription

        # Via partner (last active subscription)
        return self.env['saas.subscription'].search([
            ('partner_id', '=', invoice.partner_id.id),
            ('state', '=', 'active'),
        ], limit=1, order='id desc')
