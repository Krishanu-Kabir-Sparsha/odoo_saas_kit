from odoo import http
from odoo.http import request
import logging

try:
    import stripe
except ImportError:
    stripe = None

_logger = logging.getLogger(__name__)

class StripeController(http.Controller):
    
    @http.route('/saas/payment/checkout', type='http', auth='user', website=True)
    def payment_checkout(self, subscription_id, **kwargs):
        """Redirect to Stripe Checkout"""
        subscription = request.env['saas.subscription'].browse(int(subscription_id))
        
        if not subscription.exists():
            return request.redirect('/saas/packages?error=Subscription not found')
        
        try:
            checkout_url = subscription.create_stripe_checkout_session(
                return_url=request.httprequest.url_root
            )
            
            if checkout_url:
                return request.redirect(checkout_url)
            else:
                return request.redirect('/saas/packages?error=Payment setup failed')
                
        except Exception as e:
            _logger.error(f"Checkout error: {e}")
            return request.redirect(f'/saas/packages?error={str(e)}')
    
    @http.route('/saas/payment/success', type='http', auth='public', website=True)
    def payment_success(self, session_id=None, **kwargs):
        """Payment success callback"""
        if session_id:
            # Store success in session for portal display
            request.session['payment_success'] = True
            
        return request.render('saas_payment_stripe.payment_success', {
            'session_id': session_id,
        })
    
    @http.route('/saas/payment/cancel', type='http', auth='public', website=True)
    def payment_cancel(self, **kwargs):
        """Payment cancel callback"""
        return request.render('saas_payment_stripe.payment_cancel', {})
    
    @http.route('/saas/stripe/webhook', type='http', auth='none', methods=['POST'], csrf=False)
    def stripe_webhook(self):
        """Stripe webhook endpoint"""
        payload = request.httprequest.data
        sig_header = request.httprequest.headers.get('Stripe-Signature')
        
        webhook = request.env['stripe.webhook'].sudo()
        result = webhook.process_webhook(payload, sig_header)
        
        if result:
            return http.Response(status=200)
        else:
            return http.Response(status=400)
    
    @http.route('/saas/subscription/<int:subscription_id>/pay_invoice', type='http', auth='user', website=True)
    def pay_invoice(self, subscription_id, invoice_id, **kwargs):
        """Pay a specific invoice"""
        subscription = request.env['saas.subscription'].browse(subscription_id)
        invoice = request.env['account.move'].browse(invoice_id)
        
        if not subscription or not invoice:
            return request.redirect('/my/subscriptions?error=Invalid request')
        
        try:
            # Check if customer has saved payment method
            if subscription.stripe_payment_method_id:
                result = subscription.charge_saved_payment_method(invoice_id)
                
                if result and isinstance(result, dict) and result.get('error'):
                    return request.redirect(f'/my/subscriptions?error={result["error"]}')
                elif result:
                    return request.redirect('/my/subscriptions?message=Payment successful')
            
            # Otherwise create new payment intent
            client_secret = subscription.create_payment_intent(invoice_id)
            
            if client_secret:
                return request.render('saas_payment_stripe.payment_form', {
                    'subscription': subscription,
                    'invoice': invoice,
                    'client_secret': client_secret,
                    'publishable_key': request.env['stripe.config'].get_publishable_key(),
                })
            else:
                return request.redirect('/my/subscriptions?error=Payment setup failed')
                
        except Exception as e:
            _logger.error(f"Pay invoice error: {e}")
            return request.redirect(f'/my/subscriptions?error={str(e)}')