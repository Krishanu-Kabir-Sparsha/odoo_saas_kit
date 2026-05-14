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
            base_url = request.env['ir.config_parameter'].sudo().get_param(
                'web.base.url', request.httprequest.url_root
            ).rstrip('/')
            gateway_url = subscription.create_sslcommerz_session(
                return_url=base_url
            )

            if gateway_url:
                return request.redirect(gateway_url, local=False)
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
        SSLCommerz sends payment data via POST including tran_id, status,
        val_id etc.  We process it here as a fallback in case the IPN
        notification doesn't arrive (common in sandbox mode).
        """
        tran_id = kwargs.get('tran_id', '')
        status = kwargs.get('status', '')

        _logger.info(
            f"SSLCommerz success redirect: tran_id={tran_id}, status={status}")

        # Find the transaction and subscription
        subscription = None
        if tran_id:
            transaction = request.env['sslcommerz.transaction'].sudo().search(
                [('tran_id', '=', tran_id)], limit=1)
            if transaction:
                subscription = transaction.subscription_id

                # Process payment if not already done by IPN
                if transaction.status not in ['valid', 'validated']:
                    _logger.info(
                        f"Processing payment from success redirect "
                        f"(IPN may not have arrived yet)")
                    try:
                        transaction_model = request.env[
                            'sslcommerz.transaction'].sudo()
                        post_data = dict(request.httprequest.form)
                        if post_data and post_data.get('status') == 'VALID':
                            transaction_model.process_ipn(post_data)
                    except Exception as e:
                        _logger.warning(
                            f"Success-page payment processing failed "
                            f"(IPN will handle it): {e}")

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

        Uses auth='none' because SSLCommerz sends this server-to-server
        (no user session).  We must manually select the database.
        """
        _logger.info("SSLCommerz IPN received")

        # Get POST data
        post_data = dict(request.httprequest.form)

        _logger.info(
            f"IPN data: tran_id={post_data.get('tran_id')}, "
            f"status={post_data.get('status')}")

        try:
            # auth='none' → need to ensure we have a valid registry/env.
            # Try multiple strategies to find the correct database:
            # 1. db_name from odoo config (if set explicitly)
            # 2. request.db (if Odoo resolved it from dbfilter)
            # 3. Host header (matches dbfilter = ^%h$ behavior)
            import odoo
            db_name = odoo.tools.config.get('db_name')
            if not db_name:
                db_name = getattr(request, 'db', None)
            if not db_name:
                # Fallback: use Host header (matches ^%h$ dbfilter)
                host = request.httprequest.host.split(':')[0]
                from odoo.service.db import list_dbs
                try:
                    available_dbs = list_dbs(force=True)
                    if host in available_dbs:
                        db_name = host
                    elif available_dbs:
                        # Last resort: use the first available DB
                        db_name = available_dbs[0]
                except Exception:
                    pass
            if not db_name:
                _logger.error("IPN: No database could be determined")
                return http.Response('No database', status=500)

            registry = odoo.registry(db_name)
            with registry.cursor() as cr:
                env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
                transaction_model = env['sslcommerz.transaction']
                result = transaction_model.process_ipn(post_data)
                if result:
                    cr.commit()
                    return http.Response('IPN Processed', status=200)
                else:
                    cr.commit()
                    return http.Response('IPN Processing Failed', status=400)

        except Exception as e:
            _logger.error(f"IPN processing error: {e}", exc_info=True)
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
            base_url = request.env['ir.config_parameter'].sudo().get_param(
                'web.base.url', request.httprequest.url_root
            ).rstrip('/')
            gateway_url = subscription.create_sslcommerz_session(
                return_url=base_url,
                invoice_id=invoice_id,
                purpose='invoice_pay',
            )

            if gateway_url:
                return request.redirect(gateway_url, local=False)
            else:
                return request.redirect(
                    '/my/subscriptions?error=Payment setup failed')

        except Exception as e:
            _logger.error(f"Pay invoice error: {e}")
            return request.redirect(
                f'/my/subscriptions?error={str(e)}')
