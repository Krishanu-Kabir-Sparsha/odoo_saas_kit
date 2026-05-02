from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import timedelta
import logging

_logger = logging.getLogger(__name__)

class SaasBillingMixin(models.AbstractModel):
    _name = 'saas.billing.mixin'
    _description = 'SaaS Billing Mixin - Shared billing logic'

    def _get_billing_product(self):
        """Get or create the SaaS billing product"""
        product = self.env['product.product'].search([
            ('name', '=', 'SaaS Subscription'),
            ('type', '=', 'service')
        ], limit=1)
        
        if not product:
            product = self.env['product.product'].create({
                'name': 'SaaS Subscription',
                'type': 'service',
                'invoice_policy': 'order',
                'service_type': 'manual',
                'list_price': 0.0,
                'taxes_id': False,
            })
        return product

    def _create_invoice_for_subscription(self, subscription):
        """Create an invoice for a subscription renewal"""
        self.ensure_one()
        
        # Determine amount based on billing cycle
        if subscription.billing_cycle == 'yearly':
            amount = subscription.package_id.yearly_price
        else:
            amount = subscription.package_id.monthly_price
        
        # Create or get sale order
        if not subscription.sale_order_id:
            sale_order = self._create_sale_order(subscription)
            subscription.sale_order_id = sale_order.id
        else:
            sale_order = subscription.sale_order_id
        
        # Create invoice from sale order
        invoice = self._create_invoice_from_sale_order(sale_order, amount)
        
        return invoice

    def _create_sale_order(self, subscription):
        """Create a sale order for the subscription"""
        product = self._get_billing_product()
        
        if subscription.billing_cycle == 'yearly':
            price = subscription.package_id.yearly_price
            name = f"{subscription.package_id.name} - Yearly Subscription"
        else:
            price = subscription.package_id.monthly_price
            name = f"{subscription.package_id.name} - Monthly Subscription"
        
        sale_order = self.env['sale.order'].create({
            'partner_id': subscription.partner_id.id,
            'company_id': subscription.company_id.id,
            'origin': subscription.name,
            'note': f"SaaS Subscription: {subscription.package_id.name}\nCustomer: {subscription.partner_id.name}",
            'payment_term_id': self._get_payment_term().id,
        })
        
        self.env['sale.order.line'].create({
            'order_id': sale_order.id,
            'product_id': product.id,
            'product_uom_qty': 1,
            'price_unit': price,
            'name': name,
        })
        
        sale_order.action_confirm()
        return sale_order

    def _create_invoice_from_sale_order(self, sale_order, amount):
        """Create an invoice from a sale order"""
        # Create invoice
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': sale_order.partner_id.id,
            'invoice_date': fields.Date.today(),
            'invoice_date_due': fields.Date.today() + timedelta(days=7),
            'invoice_origin': sale_order.name,
            'company_id': sale_order.company_id.id,
        })
        
        # Copy sale order lines to invoice
        for line in sale_order.order_line:
            self.env['account.move.line'].create({
                'move_id': invoice.id,
                'product_id': line.product_id.id,
                'name': line.name,
                'quantity': line.product_uom_qty,
                'price_unit': line.price_unit,
                'account_id': line.product_id.property_account_income_id.id or line.product_id.categ_id.property_account_income_categ_id.id,
            })
        
        invoice._onchange_currency()
        invoice._recompute_dynamic_lines()
        
        return invoice

    def _get_payment_term(self):
        """Get default payment term (7 days)"""
        term = self.env['account.payment.term'].search([('name', '=', '7 Days')], limit=1)
        if not term:
            term = self.env['account.payment.term'].create({
                'name': '7 Days',
                'line_ids': [(0, 0, {
                    'value': 'balance',
                    'days': 7,
                })]
            })
        return term