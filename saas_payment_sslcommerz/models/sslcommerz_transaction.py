from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging
import json
import requests
from .sslcommerz_config import (
    get_sslcommerz_store_id,
    get_sslcommerz_store_passwd,
    get_sslcommerz_api_url,
    validate_sslcommerz_hash,
)

_logger = logging.getLogger(__name__)


class SslcommerzTransaction(models.Model):
    _name = 'sslcommerz.transaction'
    _description = 'SSLCommerz Transaction Log'
    _rec_name = 'tran_id'
    _order = 'created_at desc'

    # Transaction Identification
    tran_id = fields.Char(
        string='Transaction ID', required=True, index=True,
        help='Unique transaction ID sent to SSLCommerz')
    session_key = fields.Char(
        string='Session Key', index=True,
        help='SSLCommerz session key returned at initiation')
    val_id = fields.Char(
        string='Validation ID',
        help='SSLCommerz validation ID received in IPN')
    bank_tran_id = fields.Char(
        string='Bank Transaction ID',
        help='Bank-side transaction ID')

    # Status
    status = fields.Selection([
        ('initiated', 'Initiated'),
        ('valid', 'Valid (Paid)'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
        ('unattempted', 'Unattempted'),
        ('expired', 'Expired'),
        ('validated', 'Validated (Confirmed)'),
    ], string='Status', default='initiated', index=True)

    # Amounts
    amount = fields.Float(string='Transaction Amount')
    store_amount = fields.Float(
        string='Store Amount',
        help='Amount after SSLCommerz commission deduction')
    currency = fields.Char(string='Currency', default='BDT')

    # Payment Method Details
    card_type = fields.Char(string='Card/Payment Type')
    card_no = fields.Char(string='Card Number (Masked)')
    card_brand = fields.Char(string='Card Brand')
    card_issuer = fields.Char(string='Card Issuer')

    # Risk Assessment
    risk_level = fields.Char(string='Risk Level')
    risk_title = fields.Char(string='Risk Title')

    # Linked Records
    subscription_id = fields.Many2one(
        'saas.subscription', string='Subscription',
        help='Linked SaaS subscription')
    invoice_id = fields.Many2one(
        'account.move', string='Invoice',
        help='Linked Odoo invoice')
    partner_id = fields.Many2one(
        'res.partner', string='Customer',
        help='Customer who made the payment')

    # Payload & Metadata
    request_payload = fields.Text(
        string='Request Payload',
        help='Data sent to SSLCommerz at session creation')
    ipn_payload = fields.Text(
        string='IPN Payload',
        help='Data received from SSLCommerz IPN notification')
    validation_payload = fields.Text(
        string='Validation Response',
        help='Response from SSLCommerz Order Validation API')
    error_message = fields.Text(string='Error Message')

    # Timestamps
    created_at = fields.Datetime(
        string='Created At', default=fields.Datetime.now)
    processed_at = fields.Datetime(string='Processed At')

    # Custom Value Fields (mapped from SSLCommerz value_a/b/c/d)
    # value_a = subscription_id, value_b = invoice_id,
    # value_c = partner_id, value_d = purpose (checkout/renewal/invoice_pay)

    @api.model
    def process_ipn(self, post_data):
        """
        Process incoming SSLCommerz IPN (Instant Payment Notification).
        This is the core payment processing logic.
        """
        tran_id = post_data.get('tran_id', '')
        status = post_data.get('status', '')
        val_id = post_data.get('val_id', '')

        _logger.info(
            f"SSLCommerz IPN received: tran_id={tran_id}, status={status}")

        if not tran_id:
            _logger.warning("IPN received without tran_id")
            return False

        # Find existing transaction record
        transaction = self.search([('tran_id', '=', tran_id)], limit=1)
        if not transaction:
            _logger.warning(f"Transaction {tran_id} not found in system")
            return False

        # Check if already processed
        if transaction.status in ['valid', 'validated']:
            _logger.info(
                f"Transaction {tran_id} already processed, skipping")
            return True

        # Validate the IPN hash signature
        if not validate_sslcommerz_hash(self.env, post_data):
            _logger.error(
                f"IPN hash validation failed for transaction {tran_id}")
            transaction.write({
                'status': 'failed',
                'error_message': 'IPN hash validation failed',
                'ipn_payload': json.dumps(post_data),
                'processed_at': fields.Datetime.now(),
            })
            return False

        # Update transaction record with IPN data
        update_vals = {
            'ipn_payload': json.dumps(post_data),
            'val_id': val_id,
            'bank_tran_id': post_data.get('bank_tran_id', ''),
            'card_type': post_data.get('card_type', ''),
            'card_no': post_data.get('card_no', ''),
            'card_brand': post_data.get('card_brand', ''),
            'card_issuer': post_data.get('card_issuer', ''),
            'risk_level': post_data.get('risk_level', ''),
            'risk_title': post_data.get('risk_title', ''),
            'store_amount': float(post_data.get('store_amount', 0)),
            'processed_at': fields.Datetime.now(),
        }

        if status == 'VALID':
            # Validate with SSLCommerz Order Validation API
            is_validated = self._validate_order(
                val_id, transaction.amount)

            if is_validated:
                update_vals['status'] = 'validated'
                transaction.write(update_vals)

                # Process the successful payment
                self._handle_payment_success(transaction, post_data)
                return True
            else:
                update_vals['status'] = 'failed'
                update_vals['error_message'] = \
                    'Order validation failed with SSLCommerz'
                transaction.write(update_vals)
                return False

        elif status == 'FAILED':
            update_vals['status'] = 'failed'
            update_vals['error_message'] = \
                post_data.get('error', 'Payment failed at gateway')
            transaction.write(update_vals)
            self._handle_payment_failure(transaction)
            return True

        elif status == 'CANCELLED':
            update_vals['status'] = 'cancelled'
            transaction.write(update_vals)
            return True

        elif status == 'UNATTEMPTED':
            update_vals['status'] = 'unattempted'
            transaction.write(update_vals)
            return True

        elif status == 'EXPIRED':
            update_vals['status'] = 'expired'
            transaction.write(update_vals)
            return True

        else:
            _logger.warning(
                f"Unknown IPN status: {status} for tran_id: {tran_id}")
            update_vals['error_message'] = f"Unknown status: {status}"
            transaction.write(update_vals)
            return False

    def _validate_order(self, val_id, expected_amount):
        """
        Call SSLCommerz Order Validation API to verify the transaction.
        This is a CRITICAL security step to prevent amount tampering.
        """
        if not val_id:
            return False

        store_id = get_sslcommerz_store_id(self.env)
        store_passwd = get_sslcommerz_store_passwd(self.env)
        api_url = get_sslcommerz_api_url(self.env)

        validation_url = (
            f"{api_url}/validator/api/validationserverAPI.php"
        )

        params = {
            'val_id': val_id,
            'store_id': store_id,
            'store_passwd': store_passwd,
            'format': 'json',
        }

        try:
            response = requests.get(
                validation_url, params=params, timeout=30)
            result = response.json()

            # Store validation response
            self.write({
                'validation_payload': json.dumps(result)
            })

            # Check status
            if result.get('status') in ['VALID', 'VALIDATED']:
                # Verify amount matches
                validated_amount = float(result.get('amount', 0))
                if abs(validated_amount - expected_amount) > 0.01:
                    _logger.error(
                        f"Amount mismatch! Expected: {expected_amount}, "
                        f"Got: {validated_amount}")
                    return False
                return True
            else:
                _logger.warning(
                    f"Validation failed: {result.get('status')}")
                return False

        except Exception as e:
            _logger.error(f"Order validation API error: {e}")
            return False

    def _handle_payment_success(self, transaction, post_data):
        """Handle successful payment — activate subscription, register payment"""
        subscription = transaction.subscription_id
        if not subscription:
            _logger.warning(
                f"No subscription linked to transaction {transaction.tran_id}")
            return

        # Activate subscription if pending
        if subscription.state == 'pending':
            subscription.action_activate()
            _logger.info(
                f"Subscription {subscription.name} activated via "
                f"SSLCommerz payment")

        # Reactivate if suspended
        elif subscription.state == 'suspended':
            subscription.action_activate()
            _logger.info(
                f"Subscription {subscription.name} reactivated via "
                f"SSLCommerz payment")

        # Register payment on linked invoice
        if transaction.invoice_id:
            self._register_payment(
                transaction.invoice_id, transaction.amount)

        # Also check for the latest unpaid invoice
        elif subscription.state == 'active':
            latest_invoice = self.env['account.move'].search([
                ('invoice_origin', 'ilike', subscription.name),
                ('payment_state', 'in', ['not_paid', 'partial']),
                ('move_type', '=', 'out_invoice'),
            ], order='id desc', limit=1)

            if latest_invoice:
                self._register_payment(
                    latest_invoice, transaction.amount)

    def _handle_payment_failure(self, transaction):
        """Handle failed payment"""
        subscription = transaction.subscription_id
        if not subscription:
            return

        if subscription.state == 'pending':
            subscription.write({
                'state_reason': (
                    f"Payment failed via SSLCommerz: "
                    f"{transaction.error_message or 'Unknown error'}"
                ),
            })

        _logger.warning(
            f"Payment failed for subscription {subscription.name}")

    def _register_payment(self, invoice, amount):
        """Register payment in Odoo accounting"""
        try:
            # Check if payment already exists
            existing_payment = self.env['account.payment'].search([
                ('ref', '=', f"SSLCommerz Payment for {invoice.name}")
            ], limit=1)

            if existing_payment:
                return

            # Find bank journal
            journal = self.env['account.journal'].search(
                [('type', '=', 'bank')], limit=1)

            if not journal:
                _logger.error("No bank journal found for payment registration")
                return

            # Create payment
            payment = self.env['account.payment'].create({
                'partner_id': invoice.partner_id.id,
                'amount': amount,
                'payment_type': 'inbound',
                'partner_type': 'customer',
                'ref': f"SSLCommerz Payment for {invoice.name}",
                'journal_id': journal.id,
                'date': fields.Date.today(),
            })

            payment.action_post()

            _logger.info(
                f"Registered payment of {amount} for invoice {invoice.name}")

        except Exception as e:
            _logger.error(f"Failed to register payment: {e}")
