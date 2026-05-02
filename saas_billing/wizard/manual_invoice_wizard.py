from odoo import models, fields, api, _

class ManualInvoiceWizard(models.TransientModel):
    _name = 'manual.invoice.wizard'
    _description = 'Manual Invoice Generation Wizard'

    subscription_id = fields.Many2one('saas.subscription', string='Subscription', required=True)
    invoice_date = fields.Date(string='Invoice Date', required=True, default=fields.Date.today)
    amount = fields.Float(string='Custom Amount', help='Leave empty to use package price')
    reason = fields.Text(string='Reason', required=True, help='Why is this manual invoice being generated?')
    
    def action_generate_invoice(self):
        """Generate manual invoice for subscription"""
        self.ensure_one()
        
        subscription = self.subscription_id
        
        # Determine amount
        if self.amount > 0:
            amount = self.amount
        else:
            if subscription.billing_cycle == 'yearly':
                amount = subscription.package_id.yearly_price
            else:
                amount = subscription.package_id.monthly_price
        
        # Create sale order line
        product = self._get_billing_product()
        
        sale_order = subscription.sale_order_id
        if not sale_order:
            sale_order = self.env['sale.order'].create({
                'partner_id': subscription.partner_id.id,
                'origin': f"Manual - {subscription.name}",
            })
            subscription.sale_order_id = sale_order.id
        
        sale_order_line = self.env['sale.order.line'].create({
            'order_id': sale_order.id,
            'product_id': product.id,
            'product_uom_qty': 1,
            'price_unit': amount,
            'name': f"Manual Invoice: {self.reason[:50]}",
        })
        
        # Create invoice
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': subscription.partner_id.id,
            'invoice_date': self.invoice_date,
            'invoice_origin': f"Manual for {subscription.name} - {self.reason[:30]}",
        })
        
        self.env['account.move.line'].create({
            'move_id': invoice.id,
            'product_id': product.id,
            'name': f"Manual charge: {self.reason}",
            'quantity': 1,
            'price_unit': amount,
            'account_id': product.property_account_income_id.id,
        })
        
        invoice._recompute_dynamic_lines()
        invoice.action_post()
        
        # Post message on subscription
        subscription.message_post(
            body=f"Manual invoice generated: {invoice.name} for {amount} {subscription.package_id.currency_id.symbol}<br/>Reason: {self.reason}",
            subject="Manual Invoice Created"
        )
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': invoice.id,
            'view_mode': 'form',
            'target': 'current',
        }
    
    def _get_billing_product(self):
        """Get billing product"""
        product = self.env['product.product'].search([
            ('name', '=', 'SaaS Subscription'),
            ('type', '=', 'service')
        ], limit=1)
        
        if not product:
            product = self.env['product.product'].create({
                'name': 'SaaS Subscription',
                'type': 'service',
                'list_price': 0.0,
            })
        return product