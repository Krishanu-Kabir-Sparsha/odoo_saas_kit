from odoo import http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal


class SaasCustomerPortal(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)

        # Add subscription count
        subscriptions = request.env['saas.subscription'].sudo().search([
            ('partner_id', '=', request.env.user.partner_id.id)
        ])
        values['subscription_count'] = len(subscriptions)
        values['active_subscription_count'] = len(subscriptions.filtered(lambda s: s.state == 'active'))

        # Add points balance
        points_record = request.env['saas.partner.points'].sudo().search([
            ('partner_id', '=', request.env.user.partner_id.id)
        ], limit=1)
        values['points_balance'] = points_record.balance if points_record else 0

        return values

    @http.route(['/my/subscriptions', '/my/subscriptions/page/<int:page>'], type='http', auth='user', website=True)
    def portal_my_subscriptions(self, page=1, **kwargs):
        """Customer portal - list all subscriptions"""
        values = self._prepare_portal_layout_values()

        # Get all subscriptions for this customer
        subscriptions = request.env['saas.subscription'].sudo().search([
            ('partner_id', '=', request.env.user.partner_id.id)
        ], order='id desc')

        # Pagination
        pager = request.website.pager(
            url='/my/subscriptions',
            total=len(subscriptions),
            page=page,
            step=10,
            scope=5
        )
        subscriptions = subscriptions[pager['offset']:pager['offset'] + pager['limit']]

        values.update({
            'subscriptions': subscriptions,
            'page_name': 'subscriptions',
            'pager': pager,
            'error': kwargs.get('error'),
        })
        return request.render('saas_portal.portal_my_subscriptions', values)

    @http.route('/my/subscriptions/<int:subscription_id>', type='http', auth='user', website=True)
    def portal_subscription_detail(self, subscription_id, **kwargs):
        """Subscription detail page"""
        subscription = request.env['saas.subscription'].sudo().browse(subscription_id)

        if not subscription.exists() or subscription.partner_id.id != request.env.user.partner_id.id:
            return request.redirect('/my/subscriptions')

        # Get invoices
        invoices = request.env['account.move'].sudo().search([
            ('invoice_origin', 'ilike', subscription.name),
            ('move_type', '=', 'out_invoice')
        ], order='invoice_date desc')

        # Get points transactions
        points_transactions = request.env['saas.points.transaction'].sudo().search([
            ('subscription_id', '=', subscription.id)
        ], order='date desc', limit=20)

        # Calculate points discount available
        points_balance = 0
        points_record = request.env['saas.partner.points'].sudo().search([
            ('partner_id', '=', subscription.partner_id.id)
        ], limit=1)
        if points_record:
            points_balance = points_record.balance

        config = request.env['saas.points.config'].get_config()

        # Calculate values for template
        points_value = round(points_balance * config.get('points_value_per_unit', 0.01), 2)
        min_points = config.get('min_points_redemption', 100)

        values = {
            'subscription': subscription,
            'invoices': invoices,
            'points_transactions': points_transactions,
            'points_balance': points_balance,
            'points_config': config,
            'points_value': points_value,
            'min_points': min_points,
            'page_name': 'subscription_detail',
        }
        return request.render('saas_portal.portal_subscription_detail', values)

    @http.route('/my/subscriptions/<int:subscription_id>/cancel', type='http', auth='user', methods=['POST'], website=True)
    def portal_subscription_cancel(self, subscription_id, **kwargs):
        """Cancel subscription from portal"""
        subscription = request.env['saas.subscription'].sudo().browse(subscription_id)

        if not subscription.exists() or subscription.partner_id.id != request.env.user.partner_id.id:
            return request.redirect('/my/subscriptions')

        reason = kwargs.get('reason', 'Cancelled by customer')
        subscription.action_cancel()
        subscription.write({'state_reason': reason})

        return request.redirect(f'/my/subscriptions/{subscription_id}?message=Cancelled successfully')

    @http.route('/my/subscriptions/<int:subscription_id>/reactivate', type='http', auth='user', methods=['POST'], website=True)
    def portal_subscription_reactivate(self, subscription_id, **kwargs):
        """Reactivate cancelled subscription"""
        subscription = request.env['saas.subscription'].sudo().browse(subscription_id)

        if not subscription.exists() or subscription.partner_id.id != request.env.user.partner_id.id:
            return request.redirect('/my/subscriptions')

        if subscription.state == 'canceled':
            subscription.write({'state': 'pending'})
            # Redirect to checkout for payment
            request.session['pending_subscription_id'] = subscription.id
            return request.redirect('/saas/checkout')

        return request.redirect(f'/my/subscriptions/{subscription_id}')

    @http.route('/my/invoices', type='http', auth='user', website=True)
    def portal_my_invoices(self, **kwargs):
        """Customer portal - list all invoices"""
        values = self._prepare_portal_layout_values()

        # Find all invoices linked to user's subscriptions
        subscriptions = request.env['saas.subscription'].sudo().search([
            ('partner_id', '=', request.env.user.partner_id.id)
        ])

        # Build domain for invoices from sale orders
        invoice_domains = [('id', '=', False)]  # Default empty result
        origin_names = []
        for sub in subscriptions:
            if sub.sale_order_id:
                origin_names.append(sub.sale_order_id.name)
            if sub.name:
                origin_names.append(sub.name)

        if origin_names:
            invoice_domains = ['|'] * (len(origin_names) - 1)
            for name in origin_names:
                invoice_domains.append(('invoice_origin', 'ilike', name))

        invoices = request.env['account.move'].sudo().search(
            invoice_domains + [('move_type', '=', 'out_invoice')],
            order='invoice_date desc'
        )

        values.update({
            'invoices': invoices,
            'page_name': 'invoices',
        })
        return request.render('saas_portal.portal_my_invoices', values)

    @http.route('/my/invoices/<int:invoice_id>/pdf', type='http', auth='user')
    def portal_invoice_pdf(self, invoice_id, **kwargs):
        """Download invoice PDF"""
        invoice = request.env['account.move'].sudo().browse(invoice_id)

        # Verify access
        subscription = request.env['saas.subscription'].sudo().search([
            ('sale_order_id.name', '=', invoice.invoice_origin)
        ], limit=1)

        if not subscription or subscription.partner_id.id != request.env.user.partner_id.id:
            return request.redirect('/my/invoices')

        pdf_content = request.env['ir.actions.report'].sudo()._get_report_from_name('account.account_invoices')._render_qweb_pdf([invoice.id])
        return request.make_response(pdf_content[0], headers=[
            ('Content-Type', 'application/pdf'),
            ('Content-Disposition', f'inline; filename={invoice.name}.pdf')
        ])

    @http.route('/my/points', type='http', auth='user', website=True)
    def portal_my_points(self, **kwargs):
        """Points summary page in portal"""
        values = self._prepare_portal_layout_values()

        points_record = request.env['saas.partner.points'].sudo().search([
            ('partner_id', '=', request.env.user.partner_id.id)
        ], limit=1)

        transactions = request.env['saas.points.transaction'].sudo().search([
            ('partner_id', '=', request.env.user.partner_id.id)
        ], order='date desc', limit=50)

        config = request.env['saas.points.config'].get_config()
        balance = points_record.balance if points_record else 0
        points_value = round(balance * config.get('points_value_per_unit', 0.01), 2)
        min_points = config.get('min_points_redemption', 100)

        values.update({
            'points_balance': balance,
            'transactions': transactions,
            'points_config': config,
            'points_value': points_value,
            'min_points': min_points,
            'page_name': 'points',
        })
        return request.render('saas_portal.portal_my_points', values)
