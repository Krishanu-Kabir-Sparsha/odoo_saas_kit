from odoo import http
from odoo.http import request
import logging
import json

_logger = logging.getLogger(__name__)


class SslcommerzController(http.Controller):

    @http.route('/saas/payment/checkout', type='http', auth='user',
                website=True)
    def payment_checkout(self, subscription_id, **kwargs):
        """Redirect to SSLCommerz payment gateway"""
        subscription = request.env['saas.subscription'].browse(
            int(subscription_id))

        if not subscription.exists():
            return request.redirect(
                '/saas/packages?error=Subscription not found')

        try:
            gateway_url = subscription.create_sslcommerz_session(
                return_url=request.httprequest.url_root.rstrip('/')
            )

            if gateway_url:
                return request.redirect(gateway_url)
            else:
                return request.redirect(
                    '/saas/packages?error=Payment setup failed')

        except Exception as e:
            _logger.error(f"Checkout error: {e}")
            return request.redirect(
                f'/saas/packages?error={str(e)}')

    @http.route('/saas/payment/success', type='http', auth='public',
                website=True, methods=['GET', 'POST'], csrf=False)
    def payment_success(self, **kwargs):
        """
        SSLCommerz redirects here on successful payment.
        Note: The actual payment processing happens via IPN.
        This page just shows a success message.
        """
        tran_id = kwargs.get('tran_id', '')

        # Find the transaction and subscription
        subscription = None
        if tran_id:
            transaction = request.env['sslcommerz.transaction'].sudo().search(
                [('tran_id', '=', tran_id)], limit=1)
            if transaction:
                subscription = transaction.subscription_id

        # Also try from value_a (subscription_id)
        if not subscription and kwargs.get('value_a'):
            try:
                sub_id = int(kwargs['value_a'])
                subscription = request.env['saas.subscription'].sudo().browse(
                    sub_id)
            except (ValueError, TypeError):
                pass

        return request.render(
            'saas_payment_sslcommerz.payment_success', {
                'subscription': subscription,
                'tran_id': tran_id,
            })

    @http.route('/saas/payment/fail', type='http', auth='public',
                website=True, methods=['GET', 'POST'], csrf=False)
    def payment_fail(self, **kwargs):
        """SSLCommerz redirects here on failed payment"""
        error_msg = kwargs.get('error', 'Payment failed. Please try again.')
        tran_id = kwargs.get('tran_id', '')

        return request.render(
            'saas_payment_sslcommerz.payment_fail', {
                'error': error_msg,
                'tran_id': tran_id,
            })

    @http.route('/saas/payment/cancel', type='http', auth='public',
                website=True, methods=['GET', 'POST'], csrf=False)
    def payment_cancel(self, **kwargs):
        """SSLCommerz redirects here on cancelled payment"""
        return request.render(
            'saas_payment_sslcommerz.payment_cancel', {})

    @http.route('/saas/sslcommerz/ipn', type='http', auth='none',
                methods=['POST'], csrf=False)
    def sslcommerz_ipn(self, **kwargs):
        """
        SSLCommerz IPN (Instant Payment Notification) endpoint.
        This is the critical endpoint that receives payment confirmations.
        Must be configured in SSLCommerz merchant panel.
        """
        _logger.info("SSLCommerz IPN received")

        # Get POST data
        post_data = dict(request.httprequest.form)

        _logger.info(
            f"IPN data: tran_id={post_data.get('tran_id')}, "
            f"status={post_data.get('status')}")

        try:
            transaction_model = request.env[
                'sslcommerz.transaction'].sudo()
            result = transaction_model.process_ipn(post_data)

            if result:
                return http.Response('IPN Processed', status=200)
            else:
                return http.Response('IPN Processing Failed', status=400)

        except Exception as e:
            _logger.error(f"IPN processing error: {e}")
            return http.Response(
                f'IPN Error: {str(e)}', status=500)

    @http.route(
        '/saas/subscription/<int:subscription_id>/pay_invoice',
        type='http', auth='user', website=True)
    def pay_invoice(self, subscription_id, invoice_id=None, **kwargs):
        """Pay a specific invoice via SSLCommerz"""
        subscription = request.env['saas.subscription'].browse(
            subscription_id)

        if not subscription.exists():
            return request.redirect(
                '/my/subscriptions?error=Subscription not found')

        if not invoice_id:
            return request.redirect(
                '/my/subscriptions?error=No invoice specified')

        invoice_id = int(invoice_id)
        invoice = request.env['account.move'].browse(invoice_id)

        if not invoice.exists():
            return request.redirect(
                '/my/subscriptions?error=Invoice not found')

        try:
            gateway_url = subscription.create_sslcommerz_session(
                return_url=request.httprequest.url_root.rstrip('/'),
                invoice_id=invoice_id,
                purpose='invoice_pay',
            )

            if gateway_url:
                return request.redirect(gateway_url)
            else:
                return request.redirect(
                    '/my/subscriptions?error=Payment setup failed')

        except Exception as e:
            _logger.error(f"Pay invoice error: {e}")
            return request.redirect(
                f'/my/subscriptions?error={str(e)}')
