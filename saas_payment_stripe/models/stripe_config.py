from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

try:
    import stripe
except ImportError:
    stripe = None

_logger = logging.getLogger(__name__)

class StripeConfig(models.TransientModel):
    _name = 'stripe.config'
    _description = 'Stripe Configuration Wizard'

    secret_key = fields.Char(string='Secret Key', required=True, help='Stripe API Secret Key')
    publishable_key = fields.Char(string='Publishable Key', required=True, help='Stripe API Publishable Key')
    webhook_secret = fields.Char(string='Webhook Secret', help='Stripe Webhook Signing Secret')
    webhook_url = fields.Char(string='Webhook URL', compute='_compute_webhook_url', readonly=True)
    
    @api.depends()
    def _compute_webhook_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for record in self:
            record.webhook_url = f"{base_url}/saas/stripe/webhook"
    
    def action_save_config(self):
        """Save Stripe configuration to system parameters"""
        self.ensure_one()
        
        # Test the secret key
        try:
            stripe.api_key = self.secret_key
            stripe.Account.retrieve()
        except Exception as e:
            raise UserError(_(f"Invalid Stripe API key: {str(e)}"))
        
        self.env['ir.config_parameter'].sudo().set_param('saas.stripe.secret_key', self.secret_key)
        self.env['ir.config_parameter'].sudo().set_param('saas.stripe.publishable_key', self.publishable_key)
        
        if self.webhook_secret:
            self.env['ir.config_parameter'].sudo().set_param('saas.stripe.webhook_secret', self.webhook_secret)
        
        return {
            'type': 'ir.actions.act_window_close',
        }


def get_stripe_secret_key(env):
    """Helper function to get Stripe secret key"""
    return env['ir.config_parameter'].sudo().get_param('saas.stripe.secret_key', default='')


def get_stripe_publishable_key(env):
    """Helper function to get Stripe publishable key"""
    return env['ir.config_parameter'].sudo().get_param('saas.stripe.publishable_key', default='')


def get_stripe_webhook_secret(env):
    """Helper function to get Stripe webhook secret"""
    return env['ir.config_parameter'].sudo().get_param('saas.stripe.webhook_secret', default='')