from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class SaasDurationDiscount(models.Model):
    _name = 'saas.duration.discount'
    _description = 'SaaS Duration-Based Discount'
    _order = 'sequence, duration_months'
    _rec_name = 'label'

    package_id = fields.Many2one(
        'saas.package', string='Package',
        required=True, ondelete='cascade',
    )
    duration_months = fields.Integer(
        string='Duration (Months)', required=True, default=1,
        help='Commitment duration in months. E.g. 1 = Monthly, 3 = Quarterly, 6 = Semi-annual, 12 = Annual',
    )
    label = fields.Char(
        string='Label', required=True, default='Monthly',
        help='Display label shown to the customer, e.g. "3 Months", "12 Months"',
    )
    discount_percent = fields.Float(
        string='Discount (%)', default=0.0,
        help='Percentage discount for this duration. E.g. 5 = 5% off the monthly price.',
    )
    is_active = fields.Boolean(string='Active', default=True)
    sequence = fields.Integer(string='Sequence', default=10)

    # ── Constraints ─────────────────────────────────────────
    @api.constrains('discount_percent')
    def _check_discount_percent(self):
        for rec in self:
            if rec.discount_percent < 0 or rec.discount_percent > 100:
                raise ValidationError(
                    _('Discount percentage must be between 0 and 100.')
                )

    @api.constrains('duration_months')
    def _check_duration_months(self):
        for rec in self:
            if rec.duration_months < 1:
                raise ValidationError(
                    _('Duration must be at least 1 month.')
                )

    _sql_constraints = [
        ('unique_duration_per_package',
         'unique(package_id, duration_months)',
         'Each duration can only appear once per package.'),
    ]

    # ── Business Logic ──────────────────────────────────────
    def get_pricing(self, base_monthly_price):
        """Calculate pricing details for this duration tier.

        Returns dict:
            base_price        – original monthly price
            discount_percent  – percentage off
            discount_amount   – currency amount saved per month
            monthly_price     – effective monthly price after discount
            total_price       – total for the full duration
            duration_months   – number of months
            label             – display label
        """
        self.ensure_one()
        discount_amt = base_monthly_price * (self.discount_percent / 100.0)
        monthly_after = base_monthly_price - discount_amt
        total = monthly_after * self.duration_months
        return {
            'base_price': base_monthly_price,
            'discount_percent': self.discount_percent,
            'discount_amount': round(discount_amt, 2),
            'monthly_price': round(monthly_after, 2),
            'total_price': round(total, 2),
            'duration_months': self.duration_months,
            'label': self.label,
        }
