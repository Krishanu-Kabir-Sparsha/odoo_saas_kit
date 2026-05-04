from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging
import json
from .stripe_config import get_stripe_webhook_secret

try:
    import stripe
except ImportError:
    stripe = None
    logging.getLogger(__name__).warning("stripe library not installed. Install with: pip install stripe")

_logger = logging.getLogger(__name__)

class StripeWebhook(models.Model):
    _name = 'stripe.webhook'
    _description = 'Stripe Webhook Log'
    _rec_name = 'event_type'

    event_id = fields.Char(string='Event ID', required=True, index=True)
    event_type = fields.Char(string='Event Type', required=True)
    payload = fields.Text(string='Payload')
    processed = fields.Boolean(string='Processed', default=False)
    processed_at = fields.Datetime(string='Processed At')
    error_message = fields.Text(string='Error Message')
    created_at = fields.Datetime(string='Created At', default=fields.Datetime.now)
    
    @api.model
    def process_webhook(self, payload, sig_header):
        """Process incoming Stripe webhook"""
        webhook_secret = get_stripe_webhook_secret(self.env)
        
        if not webhook_secret:
            _logger.warning("Webhook secret not configured")
            return False
        
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, webhook_secret
            )
        except ValueError as e:
            _logger.error(f"Invalid payload: {e}")
            return False
        except stripe.error.SignatureVerificationError as e:
            _logger.error(f"Invalid signature: {e}")
            return False
        
        # Log webhook
        webhook_log = self.create({
            'event_id': event['id'],
            'event_type': event['type'],
            'payload': json.dumps(event),
            'processed': False,
        })
        
        # Process based on event type
        try:
            if event['type'] == 'checkout.session.completed':
                self._handle_checkout_completed(event['data']['object'])
            elif event['type'] == 'invoice.payment_succeeded':
                self._handle_invoice_payment_succeeded(event['data']['object'])
            elif event['type'] == 'invoice.payment_failed':
                self._handle_invoice_payment_failed(event['data']['object'])
            elif event['type'] == 'customer.subscription.deleted':
                self._handle_subscription_deleted(event['data']['object'])
            elif event['type'] == 'payment_intent.succeeded':
                self._handle_payment_intent_succeeded(event['data']['object'])
            elif event['type'] == 'payment_intent.payment_failed':
                self._handle_payment_intent_failed(event['data']['object'])
            
            webhook_log.write({
                'processed': True,
                'processed_at': fields.Datetime.now()
            })
            
        except Exception as e:
            webhook_log.write({
                'error_message': str(e),
                'processed_at': fields.Datetime.now()
            })
            _logger.error(f"Webhook processing failed: {e}")
            raise
        
        return True
    
    def _handle_checkout_completed(self, session):
        """Handle successful checkout session"""
        subscription_id = session.get('metadata', {}).get('subscription_id')
        
        if not subscription_id:
            _logger.warning("No subscription_id in checkout session metadata")
            return
        
        subscription = self.env['saas.subscription'].browse(int(subscription_id))
        
        if not subscription.exists():
            _logger.warning(f"Subscription {subscription_id} not found")
            return
        
        # Update subscription with payment method
        if session.get('payment_method_types'):
            subscription.stripe_payment_method_id = session.get('payment_method')
        
        # Activate subscription
        subscription.action_activate()
        
        _logger.info(f"Checkout completed for subscription {subscription.name}")
    
    def _handle_invoice_payment_succeeded(self, invoice):
        """Handle successful invoice payment"""
        subscription_id = invoice.get('metadata', {}).get('subscription_id')
        
        if subscription_id:
            subscription = self.env['saas.subscription'].browse(int(subscription_id))
            
            if subscription.exists():
                # Find the invoice in Odoo
                odoo_invoice = self.env['account.move'].search([
                    ('invoice_origin', 'ilike', subscription.name)
                ], order='id desc', limit=1)
                
                if odoo_invoice and odoo_invoice.payment_state != 'paid':
                    # Register payment
                    self._register_payment(odoo_invoice, invoice.get('amount_paid', 0) / 100)
                
                # If subscription was suspended, reactivate
                if subscription.state == 'suspended':
                    subscription.action_activate()
                
                _logger.info(f"Invoice payment succeeded for {subscription.name}")
    
    def _handle_invoice_payment_failed(self, invoice):
        """Handle failed invoice payment"""
        subscription_id = invoice.get('metadata', {}).get('subscription_id')
        
        if subscription_id:
            subscription = self.env['saas.subscription'].browse(int(subscription_id))
            
            if subscription.exists() and subscription.state == 'active':
                subscription.action_suspend()
                _logger.warning(f"Subscription {subscription.name} suspended due to payment failure")
    
    def _handle_subscription_deleted(self, subscription_stripe):
        """Handle Stripe subscription deletion"""
        # Find Odoo subscription by Stripe subscription ID
        odoo_subscription = self.env['saas.subscription'].search([
            ('stripe_subscription_id', '=', subscription_stripe.get('id'))
        ], limit=1)
        
        if odoo_subscription and odoo_subscription.state != 'canceled':
            odoo_subscription.action_cancel()
            _logger.info(f"Subscription {odoo_subscription.name} canceled via Stripe")
    
    def _handle_payment_intent_succeeded(self, payment_intent):
        """Handle successful payment intent"""
        metadata = payment_intent.get('metadata', {})
        invoice_id = metadata.get('invoice_id')
        
        if invoice_id:
            invoice = self.env['account.move'].browse(int(invoice_id))
            if invoice and invoice.payment_state != 'paid':
                amount = payment_intent.get('amount', 0) / 100
                self._register_payment(invoice, amount)
    
    def _handle_payment_intent_failed(self, payment_intent):
        """Handle failed payment intent"""
        metadata = payment_intent.get('metadata', {})
        subscription_id = metadata.get('subscription_id')
        
        if subscription_id:
            subscription = self.env['saas.subscription'].browse(int(subscription_id))
            
            if subscription and subscription.state == 'pending':
                subscription.write({
                    'state_reason': f"Payment failed: {payment_intent.get('last_payment_error', {}).get('message', 'Unknown error')}"
                })
    
    def _register_payment(self, invoice, amount):
        """Register payment in Odoo"""
        try:
            # Check if payment already exists
            existing_payment = self.env['account.payment'].search([
                ('ref', '=', f"Stripe Payment for {invoice.name}")
            ], limit=1)
            
            if existing_payment:
                return
            
            # Create payment
            payment = self.env['account.payment'].create({
                'partner_id': invoice.partner_id.id,
                'amount': amount,
                'payment_type': 'inbound',
                'partner_type': 'customer',
                'ref': f"Stripe Payment for {invoice.name}",
                'journal_id': self.env['account.journal'].search([('type', '=', 'bank')], limit=1).id,
                'payment_date': fields.Date.today(),
            })
            
            payment.action_post()
            
            # Reconcile with invoice
            payment.write({
                'reconciled_invoice_ids': [(4, invoice.id)]
            })
            
            _logger.info(f"Registered payment of {amount} for invoice {invoice.name}")
            
        except Exception as e:
            _logger.error(f"Failed to register payment: {e}")