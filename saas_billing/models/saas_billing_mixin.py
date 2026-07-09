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
        """Create an invoice from a sale order (Odoo 18 compatible).

        Invoice lines MUST be created together with the move via
        ``invoice_line_ids`` tuples — creating ``account.move.line``
        records separately after the move exists will fail in Odoo 18.
        """
        product = self._get_billing_product()

        # Build invoice line tuples from sale order lines
        invoice_lines = []
        for line in sale_order.order_line:
            invoice_lines.append((0, 0, {
                'product_id': line.product_id.id,
                'name': line.name,
                'quantity': line.product_uom_qty,
                'price_unit': line.price_unit,
            }))

        # Fall back to a single line if sale order has no lines
        if not invoice_lines:
            invoice_lines = [(0, 0, {
                'product_id': product.id,
                'name': f"SaaS Subscription Renewal",
                'quantity': 1,
                'price_unit': amount,
            })]

        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': sale_order.partner_id.id,
            'invoice_date': fields.Date.today(),
            'invoice_date_due': fields.Date.today() + timedelta(days=7),
            'invoice_origin': sale_order.name,
            'company_id': sale_order.company_id.id,
            'invoice_line_ids': invoice_lines,
        })

        return invoice

    def _get_payment_term(self):
        """Get default payment term (7 days)"""
        term = self.env['account.payment.term'].search([('name', '=', '7 Days')], limit=1)
        if not term:
            term = self.env['account.payment.term'].create({
                'name': '7 Days',
                'line_ids': [(0, 0, {
                    'value': 'balance',
                    'nb_days': 7,
                })]
            })
        return term

    def _create_initial_invoice(self, subscription, charged_amount):
        """Create + post a PAID initial invoice for a customer's FIRST purchase.

        Itemized to mirror the checkout breakdown and reconstructed so the lines
        total EXACTLY ``charged_amount`` (what SSLCommerz collected): a
        subscription line, a one-time setup-fee line (if any), and a negative
        loyalty-points line (if points were redeemed). Tax-free. The caller
        registers the payment against it so it lands as PAID.
        """
        product = self._get_billing_product()
        duration = subscription.duration_months or 1
        pricing = subscription.package_id.get_duration_pricing(duration)
        subtotal = round(pricing.get('total_price', 0.0), 2)
        setup_fee = round(subscription.package_id.setup_fee or 0.0, 2)
        charged = round(charged_amount or 0.0, 2)
        # Whatever is left after subscription + setup fee is the points discount,
        # so the invoice total reconciles exactly to what was actually charged.
        points_discount = round((subtotal + setup_fee) - charged, 2)

        term = '%d month%s' % (duration, 's' if duration > 1 else '')
        lines = [(0, 0, {
            'product_id': product.id,
            'name': '%s — %s subscription' % (subscription.package_id.name, term),
            'quantity': 1,
            'price_unit': subtotal,
            'tax_ids': [(6, 0, [])],
        })]
        if setup_fee > 0.01:
            lines.append((0, 0, {
                'product_id': product.id,
                'name': 'Setup fee (one-time)',
                'quantity': 1,
                'price_unit': setup_fee,
                'tax_ids': [(6, 0, [])],
            }))
        if points_discount > 0.01:
            lines.append((0, 0, {
                'product_id': product.id,
                'name': 'Loyalty points redemption',
                'quantity': 1,
                'price_unit': -points_discount,
                'tax_ids': [(6, 0, [])],
            }))

        move_vals = {
            'move_type': 'out_invoice',
            'partner_id': subscription.partner_id.id,
            'invoice_date': fields.Date.today(),
            'invoice_date_due': fields.Date.today(),
            'invoice_origin': subscription.name,
            'company_id': subscription.company_id.id,
            'invoice_line_ids': lines,
        }
        if 'saas_subscription_id' in self.env['account.move']._fields:
            move_vals['saas_subscription_id'] = subscription.id

        invoice = self.env['account.move'].create(move_vals)
        invoice.action_post()
        _logger.info(
            "Initial invoice %s created for %s (total %s)",
            invoice.name, subscription.name, invoice.amount_total)
        return invoice