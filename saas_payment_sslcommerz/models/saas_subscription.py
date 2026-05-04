from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging
import uuid
import requests
import json
from .sslcommerz_config import (
    get_sslcommerz_store_id,
    get_sslcommerz_store_passwd,
    get_sslcommerz_api_url,
)

_logger = logging.getLogger(__name__)


class SaasSubscription(models.Model):
    _inherit = 'saas.subscription'

    sslcommerz_tran_ids = fields.One2many(
        'sslcommerz.transaction', 'subscription_id',
        string='SSLCommerz Transactions', copy=False)
    last_sslcommerz_tran_id = fields.Char(
        string='Last Transaction ID', copy=False,
        help='Last SSLCommerz transaction ID for this subscription')

    def create_sslcommerz_session(self, return_url=None, invoice_id=None,
                                  purpose='checkout'):
        """
        Create SSLCommerz payment session and return the gateway URL.

        Args:
            return_url: Base URL for success/fail/cancel redirects
            invoice_id: Optional invoice ID for invoice-specific payments
            purpose: 'checkout' (new sub), 'renewal', or 'invoice_pay'

        Returns:
            str: Gateway URL to redirect the customer to
        """
        self.ensure_one()

        store_id = get_sslcommerz_store_id(self.env)
        store_passwd = get_sslcommerz_store_passwd(self.env)
        api_url = get_sslcommerz_api_url(self.env)

        if not store_id or not store_passwd:
            raise UserError(_(
                'SSLCommerz is not configured. '
                'Please contact the administrator.'))

        # Determine amount
        if invoice_id:
            invoice = self.env['account.move'].browse(invoice_id)
            amount = invoice.amount_total
            currency = invoice.currency_id.name
        else:
            if self.billing_cycle == 'yearly':
                amount = self.package_id.yearly_price
            else:
                amount = self.package_id.monthly_price

            # Add setup fee for first checkout
            if purpose == 'checkout':
                amount += self.package_id.setup_fee

            currency = (self.package_id.currency_id.name
                        if self.package_id.currency_id else 'BDT')

        # Generate unique transaction ID
        tran_id = f"SAAS-{self.name}-{uuid.uuid4().hex[:8]}".upper()

        # Build base URL
        base_url = return_url or self.get_base_url()
        if base_url.endswith('/'):
            base_url = base_url[:-1]

        # Prepare SSLCommerz session data
        session_data = {
            'store_id': store_id,
            'store_passwd': store_passwd,
            'total_amount': str(round(amount, 2)),
            'currency': currency,
            'tran_id': tran_id,

            # Redirect URLs
            'success_url': f"{base_url}/saas/payment/success",
            'fail_url': f"{base_url}/saas/payment/fail",
            'cancel_url': f"{base_url}/saas/payment/cancel",
            'ipn_url': f"{base_url}/saas/sslcommerz/ipn",

            # Customer Information
            'cus_name': self.partner_id.name or 'Customer',
            'cus_email': self.partner_id.email or 'no-email@example.com',
            'cus_add1': self.partner_id.street or 'N/A',
            'cus_city': self.partner_id.city or 'Dhaka',
            'cus_country': (self.partner_id.country_id.name
                            if self.partner_id.country_id
                            else 'Bangladesh'),
            'cus_phone': self.partner_id.phone or '01700000000',

            # Product Information
            'product_name': f"{self.package_id.name} - {self.billing_cycle}",
            'product_category': 'SaaS Subscription',
            'product_profile': 'non-physical-goods',
            'shipping_method': 'NO',

            # Custom values for tracking
            # value_a = subscription_id
            # value_b = invoice_id (if applicable)
            # value_c = partner_id
            # value_d = purpose
            'value_a': str(self.id),
            'value_b': str(invoice_id) if invoice_id else '',
            'value_c': str(self.partner_id.id),
            'value_d': purpose,
        }

        # Create transaction record before calling API
        transaction = self.env['sslcommerz.transaction'].create({
            'tran_id': tran_id,
            'subscription_id': self.id,
            'invoice_id': invoice_id if invoice_id else False,
            'partner_id': self.partner_id.id,
            'amount': amount,
            'currency': currency,
            'status': 'initiated',
            'request_payload': json.dumps(session_data, default=str),
        })

        # Call SSLCommerz Session API
        try:
            response = requests.post(
                f"{api_url}/gwprocess/v4/api.php",
                data=session_data,
                timeout=30,
            )
            result = response.json()

            if result.get('status') == 'SUCCESS':
                gateway_url = result.get('GatewayPageURL')
                session_key = result.get('sessionkey')

                transaction.write({
                    'session_key': session_key,
                })

                self.last_sslcommerz_tran_id = tran_id

                _logger.info(
                    f"SSLCommerz session created for subscription "
                    f"{self.name}: tran_id={tran_id}")

                return gateway_url

            else:
                error_msg = result.get(
                    'failedreason', 'Unknown error from SSLCommerz')
                transaction.write({
                    'status': 'failed',
                    'error_message': error_msg,
                })
                raise UserError(_(
                    f'Payment session creation failed: {error_msg}'))

        except requests.exceptions.RequestException as e:
            transaction.write({
                'status': 'failed',
                'error_message': str(e),
            })
            _logger.error(f"SSLCommerz API error: {e}")
            raise UserError(_(
                f'Could not connect to payment gateway: {str(e)}'))

    def create_payment_session_for_invoice(self, invoice_id):
        """
        Create SSLCommerz payment session for a specific invoice.
        Used for recurring billing payments.
        """
        self.ensure_one()
        return self.create_sslcommerz_session(
            invoice_id=invoice_id,
            purpose='invoice_pay',
        )
