from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging
import requests
import hashlib

_logger = logging.getLogger(__name__)

# SSLCommerz API URLs
SSLCOMMERZ_SANDBOX_URL = 'https://sandbox.sslcommerz.com'
SSLCOMMERZ_LIVE_URL = 'https://securepay.sslcommerz.com'


class SslcommerzConfig(models.TransientModel):
    _name = 'sslcommerz.config'
    _description = 'SSLCommerz Configuration Wizard'

    store_id = fields.Char(
        string='Store ID', required=True,
        help='SSLCommerz Store ID (e.g., testbox or your live store ID)')
    store_passwd = fields.Char(
        string='Store Password', required=True,
        help='SSLCommerz Store Password (Secret Key)')
    is_sandbox = fields.Boolean(
        string='Sandbox Mode', default=True,
        help='Enable sandbox/test mode. Disable for live transactions.')
    ipn_url = fields.Char(
        string='IPN URL', compute='_compute_ipn_url', readonly=True,
        help='Configure this URL in your SSLCommerz merchant panel')

    @api.depends()
    def _compute_ipn_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for record in self:
            record.ipn_url = f"{base_url}/saas/sslcommerz/ipn"

    def action_save_config(self):
        """Save SSLCommerz configuration to system parameters"""
        self.ensure_one()

        # Test the credentials by calling the session API with minimal data
        try:
            api_url = self._get_api_base_url() + '/gwprocess/v4/api.php'
            test_data = {
                'store_id': self.store_id,
                'store_passwd': self.store_passwd,
                'total_amount': '10',
                'currency': 'BDT',
                'tran_id': 'TEST_VALIDATION_CHECK',
                'success_url': 'http://localhost/success',
                'fail_url': 'http://localhost/fail',
                'cancel_url': 'http://localhost/cancel',
                'cus_name': 'Test',
                'cus_email': 'test@test.com',
                'cus_add1': 'Dhaka',
                'cus_city': 'Dhaka',
                'cus_country': 'Bangladesh',
                'cus_phone': '01700000000',
                'shipping_method': 'NO',
                'product_name': 'Test',
                'product_category': 'SaaS',
                'product_profile': 'non-physical-goods',
            }
            response = requests.post(api_url, data=test_data, timeout=30)
            result = response.json()

            if result.get('status') != 'SUCCESS':
                raise UserError(_(
                    f"SSLCommerz credential validation failed: "
                    f"{result.get('failedreason', 'Unknown error')}"
                ))

        except requests.exceptions.RequestException as e:
            raise UserError(_(f"Cannot connect to SSLCommerz: {str(e)}"))

        # Save to system parameters
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('saas.sslcommerz.store_id', self.store_id)
        ICP.set_param('saas.sslcommerz.store_passwd', self.store_passwd)
        ICP.set_param('saas.sslcommerz.is_sandbox',
                       'true' if self.is_sandbox else 'false')

        return {
            'type': 'ir.actions.act_window_close',
        }

    def _get_api_base_url(self):
        """Get API base URL based on sandbox mode"""
        if self.is_sandbox:
            return SSLCOMMERZ_SANDBOX_URL
        return SSLCOMMERZ_LIVE_URL


# ==================== HELPER FUNCTIONS ====================

def get_sslcommerz_store_id(env):
    """Helper function to get SSLCommerz store ID"""
    return env['ir.config_parameter'].sudo().get_param(
        'saas.sslcommerz.store_id', default='')


def get_sslcommerz_store_passwd(env):
    """Helper function to get SSLCommerz store password"""
    return env['ir.config_parameter'].sudo().get_param(
        'saas.sslcommerz.store_passwd', default='')


def is_sslcommerz_sandbox(env):
    """Helper function to check if sandbox mode is enabled"""
    return env['ir.config_parameter'].sudo().get_param(
        'saas.sslcommerz.is_sandbox', default='true') == 'true'


def get_sslcommerz_api_url(env):
    """Get the correct API base URL"""
    if is_sslcommerz_sandbox(env):
        return SSLCOMMERZ_SANDBOX_URL
    return SSLCOMMERZ_LIVE_URL


def validate_sslcommerz_hash(env, post_data):
    """
    Validate the IPN hash signature from SSLCommerz.
    Returns True if the hash is valid, False otherwise.
    """
    store_passwd = get_sslcommerz_store_passwd(env)
    if not store_passwd:
        return False

    verify_sign = post_data.get('verify_sign', '')
    verify_key = post_data.get('verify_key', '')

    if not verify_sign or not verify_key:
        return False

    # Build the hash string from verify_key fields
    key_list = verify_key.split(',')
    hash_string = ''
    for key in sorted(key_list):
        val = post_data.get(key, '')
        hash_string += f"{key}={val}&"

    # Compute MD5 hash
    computed_hash = hashlib.md5(hash_string.encode()).hexdigest()

    return computed_hash == verify_sign
