from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import json
import logging
from markupsafe import Markup

_logger = logging.getLogger(__name__)

# AIHR AI tiers → the fixed set of AI model keys each one unlocks. Mirrors AIHR's
# own manifest_data.TIERS (the model keys are canonical and match the adapters in
# perfecthr_ai_core). Enforced per tenant by the AIHR Control Plane; surfaced/gated
# in-tenant by perfecthr_ai_core via the 'perfecthr_ai.allowed_models' parameter.
AIHR_TIER_MODELS = {
    'essential': ['hr_chatbot'],
    'professional': ['hr_chatbot', 'performance_management', 'learning_and_development'],
    'enterprise': [
        'cv_matcher', 'hr_chatbot', 'performance_management',
        'learning_and_development', 'employee_engagement_retention',
        'video_interview', 'workforce_insights',
    ],
}


class SaasPackage(models.Model):
    _name = 'saas.package'
    _description = 'SaaS Package'
    _order = 'sequence, name'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Package Name', required=True, translate=True, tracking=True)
    description = fields.Text(string='Description', translate=True, tracking=True)
    sequence = fields.Integer(string='Sequence', default=10, help='Order in listing')
    tier_level = fields.Integer(
        string='Tier Level', default=10, tracking=True,
        help='Upgrade ladder rank. A subscription can only be upgraded to a '
             'package with a STRICTLY HIGHER tier level (e.g. Basic=1, Gold=2, '
             'Advanced=3). Higher tiers should be a superset of lower-tier '
             'modules so an upgrade only ever ADDS apps.')

    # Free trial
    trial_enabled = fields.Boolean(
        string='Offer Free Trial', default=False,
        help='Show a "Start Free Trial" option for this package. The tenant is '
             'provisioned immediately with no payment; access is suspended at '
             'trial end unless the customer subscribes.')
    trial_days = fields.Integer(
        string='Trial Length (Days)', default=14,
        help='How many days the free trial lasts before payment is required.')

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

    # AIHR AI entitlement — which AI models this package includes. The 7 AIHR
    # models live inside just two Odoo modules, so module_ids can't gate them
    # individually; this tier does. Pushed to each tenant at provisioning as the
    # 'perfecthr_ai.allowed_models' parameter (see tenant.provisioner).
    aihr_tier = fields.Selection(
        [('essential', 'Essential — 1 AI model (HR Chatbot)'),
         ('professional', 'Professional — 3 AI models (+ Performance, Learning & Development)'),
         ('enterprise', 'Enterprise — all 7 AI models')],
        string='AIHR AI Tier', tracking=True,
        help='Which AIHR AI models this package unlocks. Leave empty for a package '
             'with NO AI models. The tier maps to a fixed model set (AIHR enforces '
             'the same tiers at the Control Plane); tenants on this package only see '
             'and can run those models.')

    def get_ai_allowed_models(self):
        """The list of AI model keys this package's AIHR tier unlocks (empty when
        the package includes no AI tier)."""
        self.ensure_one()
        return list(AIHR_TIER_MODELS.get(self.aihr_tier or '', []))

    # Resource Limits (shown to the customer on their in-tenant dashboard)
    storage_limit_gb = fields.Float(
        string='Storage Limit (GB)', default=20.0,
        help='Included storage quota for this tier, in GB. Shown on the tenant '
             'dashboard as used-vs-quota. 0 = unlimited (no cap displayed).')
    user_limit = fields.Integer(
        string='User Limit', default=0,
        help='Maximum internal users for this tier. Shown on the tenant '
             'dashboard as used-vs-limit. 0 = unlimited.')

    # Status
    active = fields.Boolean(string='Active', default=True, tracking=True)
    is_popular = fields.Boolean(string='Mark as Popular', default=False, help='Show badge on landing page')
    
    # Features
    feature_ids = fields.One2many('saas.package.feature', 'package_id', string='Features')
    available_feature_module_ids = fields.Many2many(
        'ir.module.module',
        compute='_compute_available_feature_module_ids',
        string='Available Feature Modules',
    )
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
    
    # Portal Display
    included_description = fields.Text(
        string='What\'s Included Description',
        translate=True,
        help='General description shown under the "What\'s Included" section on the pricing page, above the features list.'
    )
    card_footer_text = fields.Char(
        string='Card Footer Text',
        translate=True,
        help='Custom footer text for the pricing card (e.g. "Full HR Suite"). Leave blank to show default module count.'
    )

    # Image
    image_1920 = fields.Image(string='Package Image', max_width=1920, max_height=1920)
    
    @api.depends('module_ids')
    def _compute_module_count(self):
        for package in self:
            package.module_count = len(package.module_ids)

    @api.depends('module_ids', 'feature_ids', 'feature_ids.module_id')
    def _compute_available_feature_module_ids(self):
        for package in self:
            used_ids = set(package.feature_ids.mapped('module_id').ids)
            package.available_feature_module_ids = package.module_ids.filtered(
                lambda m: m.id not in used_ids
            )

    @api.onchange('feature_ids', 'module_ids')
    def _onchange_feature_ids(self):
        # Explicitly assign so the client always receives the refreshed value
        used_ids = set(self.feature_ids.mapped('module_id').ids)
        self.available_feature_module_ids = self.module_ids.filtered(
            lambda m: m.id not in used_ids
        )
    
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

    def get_pricing_duration_data(self):
        """Per-package duration-discount tiers as a JSON-ready Markup.

        Feeds the ``#durationDataJson`` island that the 'Customize Your Plan'
        calculator reads to build its duration buttons. Living on the model
        (rather than in the controller) lets the reusable
        ``saas_portal.pricing_block`` template build its own data and render
        on ANY page — e.g. the website homepage — not only on /saas/packages.

        Call on the packages to expose; an empty recordset falls back to all
        active packages.
        """
        packages = self or self.search([('active', '=', True)])
        data = {
            pkg.id: [
                {
                    'duration_months': tier.duration_months,
                    'label': tier.label,
                    'discount_percent': tier.discount_percent,
                }
                for tier in pkg.duration_discount_ids.filtered('is_active').sorted('sequence')
            ]
            for pkg in packages
        }
        return Markup(json.dumps(data))