from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

try:
    import stripe
except ImportError:
    stripe = None

_logger = logging.getLogger(__name__)

class SaasSubscription(models.Model):
    _inherit = 'saas.subscription'

    stripe_subscription_id = fields.Char(string='Stripe Subscription ID', copy=False)
    stripe_payment_method_id = fields.Char(string='Stripe Payment Method ID', copy=False)
    last_payment_intent_id = fields.Char(string='Last Payment Intent ID', copy=False)
    
    def create_stripe_checkout_session(self, return_url=None):
        """Create Stripe Checkout Session for payment"""
        self.ensure_one()
        
        secret_key = self.env['stripe.config'].get_secret_key()
        publishable_key = self.env['stripe.config'].get_publishable_key()
        
        if not secret_key or not publishable_key:
            raise UserError(_('Stripe is not configured. Please contact administrator.'))
        
        stripe.api_key = secret_key
        
        # Get or create customer
        customer_id = self.partner_id.get_or_create_stripe_customer()
        
        # Determine price based on billing cycle
        if self.billing_cycle == 'yearly':
            amount = int(self.package_id.yearly_price * 100)  # Convert to cents
            price_description = f"{self.package_id.name} - Yearly"
        else:
            amount = int(self.package_id.monthly_price * 100)
            price_description = f"{self.package_id.name} - Monthly"
        
        # Calculate setup fee
        setup_fee = int(self.package_id.setup_fee * 100)
        
        # Prepare line items
        line_items = [
            {
                'price_data': {
                    'currency': self.package_id.currency_id.name.lower(),
                    'product_data': {
                        'name': price_description,
                        'description': self.package_id.description[:255] if self.package_id.description else '',
                    },
                    'unit_amount': amount,
                    'recurring': {
                        'interval': 'month' if self.billing_cycle == 'monthly' else 'year',
                        'interval_count': 1,
                    },
                },
                'quantity': 1,
            }
        ]
        
        if setup_fee > 0:
            line_items.append({
                'price_data': {
                    'currency': self.package_id.currency_id.name.lower(),
                    'product_data': {
                        'name': 'Setup Fee',
                        'description': f'One-time setup fee for {self.package_id.name}',
                    },
                    'unit_amount': setup_fee,
                },
                'quantity': 1,
            })
        
        # Create checkout session
        try:
            session = stripe.checkout.Session.create(
                customer=customer_id,
                payment_method_types=['card'],
                line_items=line_items,
                mode='subscription' if amount > 0 else 'payment',
                success_url=f"{return_url or self.get_base_url()}/saas/payment/success?session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{return_url or self.get_base_url()}/saas/payment/cancel",
                metadata={
                    'subscription_id': self.id,
                    'subscription_name': self.name,
                    'partner_id': self.partner_id.id,
                },
                customer_update={
                    'address': 'auto',
                    'name': 'auto',
                },
            )
            
            self.stripe_subscription_id = session.get('subscription')
            return session.url
            
        except Exception as e:
            _logger.error(f"Failed to create Stripe checkout session: {e}")
            raise UserError(_(f'Payment setup failed: {str(e)}'))
    
    def create_payment_intent(self, invoice_id):
        """Create PaymentIntent for an invoice"""
        self.ensure_one()
        
        secret_key = self.env['stripe.config'].get_secret_key()
        
        if not secret_key:
            return False
        
        stripe.api_key = secret_key
        
        invoice = self.env['account.move'].browse(invoice_id)
        if not invoice:
            return False
        
        amount = int(invoice.amount_total * 100)
        
        try:
            intent = stripe.PaymentIntent.create(
                amount=amount,
                currency=invoice.currency_id.name.lower(),
                customer=self.partner_id.stripe_customer_id,
                metadata={
                    'subscription_id': self.id,
                    'invoice_id': invoice.id,
                    'invoice_number': invoice.name,
                },
                payment_method_types=['card'],
            )
            
            self.last_payment_intent_id = intent.id
            return intent.client_secret
            
        except Exception as e:
            _logger.error(f"Failed to create PaymentIntent: {e}")
            return False
    
    def charge_saved_payment_method(self, invoice_id):
        """Charge saved payment method for renewal"""
        self.ensure_one()
        
        if not self.stripe_payment_method_id:
            _logger.warning(f"No saved payment method for subscription {self.name}")
            return False
        
        secret_key = self.env['stripe.config'].get_secret_key()
        
        if not secret_key:
            return False
        
        stripe.api_key = secret_key
        
        invoice = self.env['account.move'].browse(invoice_id)
        if not invoice:
            return False
        
        amount = int(invoice.amount_total * 100)
        
        try:
            payment_intent = stripe.PaymentIntent.create(
                amount=amount,
                currency=invoice.currency_id.name.lower(),
                customer=self.partner_id.stripe_customer_id,
                payment_method=self.stripe_payment_method_id,
                off_session=True,
                confirm=True,
                metadata={
                    'subscription_id': self.id,
                    'invoice_id': invoice.id,
                    'is_renewal': 'true',
                },
            )
            
            self.last_payment_intent_id = payment_intent.id
            return payment_intent
            
        except stripe.error.CardError as e:
            _logger.warning(f"Card declined for renewal {self.name}: {e.error.message}")
            return {'error': e.error.message}
        except Exception as e:
            _logger.error(f"Failed to charge saved payment method: {e}")
            return {'error': str(e)}