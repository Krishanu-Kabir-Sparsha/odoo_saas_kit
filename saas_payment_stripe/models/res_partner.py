from odoo import models, fields, api

class ResPartner(models.Model):
    _inherit = 'res.partner'

    stripe_customer_id = fields.Char(string='Stripe Customer ID', copy=False, help='Stripe Customer ID for this customer')
    
    def get_or_create_stripe_customer(self):
        """Get existing Stripe customer or create new one"""
        self.ensure_one()
        
        if self.stripe_customer_id:
            return self.stripe_customer_id
        
        import stripe
        secret_key = self.env['stripe.config'].get_secret_key()
        
        if not secret_key:
            return False
        
        stripe.api_key = secret_key
        
        try:
            customer = stripe.Customer.create(
                email=self.email,
                name=self.name,
                metadata={
                    'odoo_partner_id': self.id,
                    'odoo_db': self.env.cr.dbname
                }
            )
            self.stripe_customer_id = customer.id
            return customer.id
        except Exception as e:
            _logger.error(f"Failed to create Stripe customer: {e}")
            return False