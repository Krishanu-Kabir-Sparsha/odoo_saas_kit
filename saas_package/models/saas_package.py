from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)

class SaasPackage(models.Model):
    _name = 'saas.package'
    _description = 'SaaS Package'
    _order = 'sequence, name'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Package Name', required=True, translate=True, tracking=True)
    description = fields.Text(string='Description', translate=True, tracking=True)
    sequence = fields.Integer(string='Sequence', default=10, help='Order in listing')
    
    # Pricing
    monthly_price = fields.Monetary(string='Monthly Price', required=True, default=0.0, currency_field='currency_id')
    yearly_price = fields.Monetary(string='Yearly Price', required=True, default=0.0, currency_field='currency_id')
    setup_fee = fields.Monetary(string='Setup Fee', default=0.0, currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', string='Currency', 
                                   default=lambda self: self.env.company.currency_id)
    
    # Modules
    module_ids = fields.Many2many(
        'ir.module.module',
        string='Selected Modules',
        help='Odoo modules included in this package',
        domain=[('state', '=', 'installed')]
    )
    module_count = fields.Integer(string='Module Count', compute='_compute_module_count', store=True)
    
    # Status
    active = fields.Boolean(string='Active', default=True, tracking=True)
    is_popular = fields.Boolean(string='Mark as Popular', default=False, help='Show badge on landing page')
    
    # Features
    feature_ids = fields.One2many('saas.package.feature', 'package_id', string='Features')
    discount_ids = fields.One2many('saas.discount', 'package_id', string='Discounts (Legacy)')
    duration_discount_ids = fields.One2many(
        'saas.duration.discount', 'package_id',
        string='Duration Discounts',
        help='Duration-based discount tiers shown on the pricing page',
    )
    
    # Statistics
    active_subscription_count = fields.Integer(
        string='Active Subscriptions',
        compute='_compute_subscription_stats',
        store=False
    )
    total_subscription_count = fields.Integer(
        string='Total Subscriptions',
        compute='_compute_subscription_stats',
        store=False
    )
    
    # Image
    image_1920 = fields.Image(string='Package Image', max_width=1920, max_height=1920)
    
    @api.depends('module_ids')
    def _compute_module_count(self):
        for package in self:
            package.module_count = len(package.module_ids)
    
    def _compute_subscription_stats(self):
        for package in self:
            if 'saas.subscription' in self.env:
                subscriptions = self.env['saas.subscription'].search([('package_id', '=', package.id)])
                package.active_subscription_count = len(subscriptions.filtered(lambda s: s.state == 'active'))
                package.total_subscription_count = len(subscriptions)
            else:
                package.active_subscription_count = 0
                package.total_subscription_count = 0
    
    @api.constrains('monthly_price', 'yearly_price', 'setup_fee')
    def _check_prices_non_negative(self):
        for package in self:
            if package.monthly_price < 0:
                raise ValidationError(_('Monthly price cannot be negative.'))
            if package.yearly_price < 0:
                raise ValidationError(_('Yearly price cannot be negative.'))
            if package.setup_fee < 0:
                raise ValidationError(_('Setup fee cannot be negative.'))
    
    def action_toggle_active(self):
        """Toggle active status"""
        for package in self:
            package.active = not package.active
    
    def action_duplicate_package(self):
        """Duplicate package with all related data"""
        self.ensure_one()
        new_package = self.copy({
            'name': f"{self.name} (Copy)",
            'active': False
        })
        # Copy features
        for feature in self.feature_ids:
            feature.copy({'package_id': new_package.id})
        # Copy discounts
        for discount in self.discount_ids:
            discount.copy({'package_id': new_package.id})
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'saas.package',
            'res_id': new_package.id,
            'view_mode': 'form',
            'target': 'current',
        }
    
    def get_price_for_cycle(self, cycle='monthly'):
        """Get price based on billing cycle"""
        self.ensure_one()
        if cycle == 'monthly':
            return self.monthly_price
        elif cycle == 'yearly':
            return self.yearly_price
        return self.monthly_price
    
    def get_discounted_price(self, cycle='monthly', coupon_code=None):
        """Calculate discounted price if applicable"""
        base_price = self.get_price_for_cycle(cycle)
        today = fields.Date.today()
        
        # Find applicable discount
        applicable_discount = self.discount_ids.filtered(
            lambda d: d.valid_from <= today <= d.valid_to
        )
        
        if not applicable_discount:
            return base_price
        
        # Apply best discount (highest value)
        best_discount = max(applicable_discount, key=lambda d: d.value)
        
        if best_discount.discount_type == 'percent':
            discounted = base_price * (1 - best_discount.value / 100)
        else:
            discounted = base_price - best_discount.value
        
        return max(0, discounted)

    def get_duration_pricing(self, duration_months=1):
        """Return full pricing breakdown for a given duration.

        Args:
            duration_months: commitment length (1, 3, 6, 12, etc.)

        Returns dict with keys:
            base_price, discount_percent, discount_amount,
            monthly_price, total_price, duration_months, duration_label,
            package_name, currency_symbol, modules
        """
        self.ensure_one()
        base = self.monthly_price
        currency_sym = self.currency_id.symbol or '৳'

        # Find the matching duration discount tier
        tier = self.duration_discount_ids.filtered(
            lambda d: d.duration_months == duration_months and d.is_active
        )

        if tier:
            tier = tier[0]
            pricing = tier.get_pricing(base)
        else:
            # No discount tier defined → 0% discount
            pricing = {
                'base_price': base,
                'discount_percent': 0.0,
                'discount_amount': 0.0,
                'monthly_price': base,
                'total_price': base * duration_months,
                'duration_months': duration_months,
                'label': f"{duration_months} Month{'s' if duration_months > 1 else ''}",
            }

        pricing.update({
            'package_id': self.id,
            'package_name': self.name,
            'currency_symbol': currency_sym,
            'setup_fee': self.setup_fee,
            'modules': [
                {'id': m.id, 'name': m.shortdesc or m.name}
                for m in self.module_ids
            ],
        })
        return pricing