from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class SaasDiscount(models.Model):
    _name = 'saas.discount'
    _description = 'SaaS Package Discount'
    _rec_name = 'display_name'
    _order = 'valid_from desc, value desc'

    package_id = fields.Many2one('saas.package', string='Package', required=True, ondelete='cascade')
    
    discount_type = fields.Selection([
        ('percent', 'Percentage (%)'),
        ('fixed', 'Fixed Amount')
    ], string='Discount Type', required=True, default='percent')
    
    value = fields.Float(string='Discount Value', required=True, help='Percentage (1-100) or fixed amount')
    
    valid_from = fields.Date(string='Valid From', required=True, default=fields.Date.today)
    valid_to = fields.Date(string='Valid To', required=True)
    
    min_commitment_months = fields.Integer(string='Minimum Commitment (Months)', default=1,
                                           help='Customer must commit to this many months')
    
    coupon_code = fields.Char(string='Coupon Code', help='Optional coupon code to activate this discount')
    usage_limit = fields.Integer(string='Usage Limit', help='Maximum number of times this discount can be used')
    used_count = fields.Integer(string='Times Used', default=0, readonly=True)
    
    display_name = fields.Char(string='Display Name', compute='_compute_display_name', store=True)
    
    @api.depends('discount_type', 'value', 'valid_from', 'valid_to')
    def _compute_display_name(self):
        for discount in self:
            type_symbol = '%' if discount.discount_type == 'percent' else f"{discount.package_id.currency_id.symbol or '$'}"
            discount.display_name = f"{discount.value}{type_symbol} off ({discount.valid_from} to {discount.valid_to})"
    
    @api.constrains('valid_from', 'valid_to')
    def _check_dates(self):
        for discount in self:
            if discount.valid_from > discount.valid_to:
                raise ValidationError(_('Valid From date cannot be after Valid To date.'))
    
    @api.constrains('value')
    def _check_value(self):
        for discount in self:
            if discount.discount_type == 'percent' and (discount.value < 0 or discount.value > 100):
                raise ValidationError(_('Percentage discount must be between 0 and 100.'))
            if discount.discount_type == 'fixed' and discount.value < 0:
                raise ValidationError(_('Fixed discount cannot be negative.'))
    
    def is_valid(self, coupon_code=None):
        """Check if discount is still valid"""
        today = fields.Date.today()
        if not (self.valid_from <= today <= self.valid_to):
            return False
        if self.coupon_code and coupon_code != self.coupon_code:
            return False
        if self.usage_limit and self.used_count >= self.usage_limit:
            return False
        return True
    
    def mark_used(self):
        """Increment usage count"""
        if self.usage_limit:
            self.used_count += 1