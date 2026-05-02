from odoo import http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal

class SaasPointsPortal(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        
        if request.env.user.partner_id:
            points_record = request.env['saas.partner.points'].search([
                ('partner_id', '=', request.env.user.partner_id.id)
            ], limit=1)
            values['points_balance'] = points_record.balance if points_record else 0
        
        return values
    
    @http.route(['/my/points'], type='http', auth='user', website=True)
    def portal_my_points(self, **kw):
        """Customer portal page for points"""
        partner = request.env.user.partner_id
        
        points_record = request.env['saas.partner.points'].search([
            ('partner_id', '=', partner.id)
        ], limit=1)
        
        transactions = request.env['saas.points.transaction'].search([
            ('partner_id', '=', partner.id)
        ], order='date desc', limit=50)
        
        config = request.env['saas.points.config'].get_config()
        
        return request.render('saas_points.portal_my_points', {
            'points_balance': points_record.balance if points_record else 0,
            'transactions': transactions,
            'config': config,
            'page_name': 'points',
        })
    
    @http.route('/my/points/redeem', type='http', auth='user', methods=['POST'], website=True)
    def portal_redeem_points(self, **post):
        """Redeem points from portal"""
        partner = request.env.user.partner_id
        points_to_redeem = int(post.get('points', 0))
        
        try:
            transaction = request.env['saas.points.transaction'].redeem_points(
                partner.id, 
                points_to_redeem
            )
            
            # Apply discount to next invoice
            # This will be handled in the checkout process
            request.session['redeemed_points'] = points_to_redeem
            
            return request.redirect('/my/points?message=Points redeemed successfully&message_type=success')
        
        except Exception as e:
            return request.redirect(f'/my/points?message={str(e)}&message_type=danger')