from odoo import http
from odoo.http import request
from odoo.exceptions import UserError
from werkzeug.exceptions import NotFound
import json
import logging

_logger = logging.getLogger(__name__)


class SaasPublicPortal(http.Controller):

    @http.route('/saas/packages', type='http', auth='public', website=True)
    def package_listing(self, **kwargs):
        """Public landing page showing all active packages"""
        packages = request.env['saas.package'].sudo().search([
            ('active', '=', True)
        ])

        # Get popular packages
        popular_packages = packages.filtered('is_popular')

        # Get billing cycle from session or default to monthly
        billing_cycle = request.session.get('billing_cycle', 'monthly')

        values = {
            'packages': packages,
            'popular_packages': popular_packages,
            'billing_cycle': billing_cycle,
            'page_name': 'packages',
        }
        return request.render('saas_portal.package_listing', values)

    @http.route('/saas/packages/json', type='json', auth='public', methods=['POST'])
    def package_listing_json(self, **kwargs):
        """Return packages as JSON for AJAX filtering"""
        domain = [('active', '=', True)]

        # Apply filters
        billing_cycle = kwargs.get('billing_cycle', 'monthly')
        min_price = float(kwargs.get('min_price', 0))
        max_price = float(kwargs.get('max_price', 1000))

        if billing_cycle == 'monthly':
            domain.append(('monthly_price', '>=', min_price))
            domain.append(('monthly_price', '<=', max_price))
        else:
            domain.append(('yearly_price', '>=', min_price))
            domain.append(('yearly_price', '<=', max_price))

        packages = request.env['saas.package'].sudo().search(domain)

        result = []
        for pkg in packages:
            result.append({
                'id': pkg.id,
                'name': pkg.name,
                'description': pkg.description[:150] if pkg.description else '',
                'monthly_price': pkg.monthly_price,
                'yearly_price': pkg.yearly_price,
                'setup_fee': pkg.setup_fee,
                'module_count': pkg.module_count,
                'is_popular': pkg.is_popular,
                'features': [{'name': f.name, 'icon': f.icon} for f in pkg.feature_ids[:5]],
                'image_url': f'/web/image/saas.package/{pkg.id}/image_1920',
            })

        return {'packages': result, 'currency': request.env.company.currency_id.symbol}

    @http.route('/saas/signup', type='http', auth='public', website=True, methods=['GET', 'POST'])
    def signup_form(self, package_id=None, **kwargs):
        """Signup form for new customers"""
        if request.httprequest.method == 'GET':
            # Show signup form
            package_id = package_id or kwargs.get('package_id')
            package = request.env['saas.package'].sudo().browse(int(package_id)) if package_id else None

            values = {
                'package': package,
                'billing_cycle': request.session.get('billing_cycle', 'monthly'),
                'error': kwargs.get('error'),
            }
            return request.render('saas_portal.signup_form', values)

        else:
            # Process signup
            name = kwargs.get('name')
            email = kwargs.get('email')
            password = kwargs.get('password')
            confirm_password = kwargs.get('confirm_password')
            company_name = kwargs.get('company_name')
            phone = kwargs.get('phone')
            package_id = kwargs.get('package_id') or request.httprequest.args.get('package_id')
            package_id = int(package_id) if package_id else None
            billing_cycle = kwargs.get('billing_cycle', 'monthly')

            # Validation
            error = None
            if not name or not email or not password:
                error = 'All required fields must be filled.'
            elif password != confirm_password:
                error = 'Passwords do not match.'
            elif len(password) < 8:
                error = 'Password must be at least 8 characters.'

            # Check if user already exists
            existing_user = request.env['res.users'].sudo().search([('login', '=', email)], limit=1)
            if existing_user:
                error = 'A user with this email already exists. Please login.'

            if error:
                return request.redirect(f'/saas/signup?package_id={package_id}&error={error}')

            # Create partner and user
            try:
                # Create partner
                partner = request.env['res.partner'].sudo().create({
                    'name': name,
                    'email': email,
                    'phone': phone,
                    'company_name': company_name or name,
                    'is_company': True if company_name else False,
                })

                # Create user
                user = request.env['res.users'].sudo().create({
                    'name': name,
                    'login': email,
                    'password': password,
                    'partner_id': partner.id,
                    'email': email,
                })

                # Create subscription
                package = request.env['saas.package'].sudo().browse(package_id)
                subscription = request.env['saas.subscription'].sudo().create({
                    'partner_id': partner.id,
                    'package_id': package.id,
                    'billing_cycle': billing_cycle,
                    'state': 'draft',
                })

                # Confirm subscription
                subscription.action_confirm()

                # Store subscription ID in session for payment
                request.session['pending_subscription_id'] = subscription.id

                # Login the user
                request.session.authenticate(request.db, email, password)

                # Redirect to checkout
                return request.redirect('/saas/checkout')

            except Exception as e:
                _logger.error(f"Signup error: {e}")
                return request.redirect(f'/saas/signup?package_id={package_id}&error=Registration failed. Please try again.')

    @http.route('/saas/checkout', type='http', auth='user', website=True)
    def checkout_page(self, **kwargs):
        """Checkout page with order summary and payment"""
        subscription_id = request.session.get('pending_subscription_id')
        if not subscription_id:
            return request.redirect('/saas/packages?error=No subscription selected')

        subscription = request.env['saas.subscription'].sudo().browse(subscription_id)
        if not subscription.exists() or subscription.state != 'pending':
            return request.redirect('/saas/packages?error=Invalid subscription')

        # Get points balance if logged in
        points_balance = 0
        points_discount = 0
        redeemed_points = 0

        if request.env.user.partner_id:
            points_record = request.env['saas.partner.points'].sudo().search([
                ('partner_id', '=', request.env.user.partner_id.id)
            ], limit=1)
            points_balance = points_record.balance if points_record else 0

        # Apply points if requested
        if kwargs.get('redeem_points'):
            points_to_redeem = int(kwargs.get('redeem_points', 0))
            if points_to_redeem <= points_balance:
                redeemed_points = points_to_redeem
                config = request.env['saas.points.config'].get_config()
                points_discount = redeemed_points * config['points_value_per_unit']

                # Store in session
                request.session['redeemed_points'] = redeemed_points

        # Calculate totals
        if subscription.billing_cycle == 'yearly':
            subtotal = subscription.package_id.yearly_price
        else:
            subtotal = subscription.package_id.monthly_price

        setup_fee = subscription.package_id.setup_fee
        total = subtotal + setup_fee - points_discount

        values = {
            'subscription': subscription,
            'subtotal': subtotal,
            'setup_fee': setup_fee,
            'points_discount': points_discount,
            'total': total,
            'points_balance': points_balance,
            'redeemed_points': redeemed_points,
            'publishable_key': request.env['ir.config_parameter'].sudo().get_param('saas.stripe.publishable_key', ''),
        }
        return request.render('saas_portal.checkout_page', values)

    @http.route('/saas/checkout/pay', type='http', auth='user', methods=['POST'], website=True)
    def checkout_pay(self, **kwargs):
        """Process payment and redirect to Stripe"""
        subscription_id = request.session.get('pending_subscription_id')
        if not subscription_id:
            return request.redirect('/saas/packages?error=No subscription selected')

        subscription = request.env['saas.subscription'].sudo().browse(subscription_id)

        # Handle points redemption
        if kwargs.get('redeem_points_checkbox'):
            points_to_redeem = int(kwargs.get('redeem_points_amount', 0))
            if points_to_redeem > 0:
                try:
                    request.env['saas.points.transaction'].sudo().redeem_points(
                        subscription.partner_id.id,
                        points_to_redeem,
                        subscription_id=subscription.id
                    )
                except Exception as e:
                    return request.redirect(f'/saas/checkout?error={str(e)}')

        # Create Stripe checkout session
        try:
            checkout_url = subscription.create_stripe_checkout_session(
                return_url=request.httprequest.url_root
            )

            if checkout_url:
                return request.redirect(checkout_url)
            else:
                return request.redirect('/saas/checkout?error=Payment setup failed')

        except Exception as e:
            _logger.error(f"Payment error: {e}")
            return request.redirect(f'/saas/checkout?error={str(e)}')

    @http.route('/saas/activation/<int:subscription_id>', type='http', auth='user', website=True)
    def activation_status(self, subscription_id, **kwargs):
        """Show provisioning status with AJAX polling"""
        subscription = request.env['saas.subscription'].sudo().browse(subscription_id)

        if not subscription.exists():
            return request.redirect('/my/subscriptions?error=Subscription not found')

        if subscription.state == 'active':
            return request.redirect(f'/saas/activation/complete/{subscription_id}')

        values = {
            'subscription': subscription,
            'tenant_url': subscription.tenant_url,
        }
        return request.render('saas_portal.activation_status', values)

    @http.route('/saas/activation/complete/<int:subscription_id>', type='http', auth='user', website=True)
    def activation_complete(self, subscription_id, **kwargs):
        """Activation complete page with tenant credentials"""
        subscription = request.env['saas.subscription'].sudo().browse(subscription_id)

        values = {
            'subscription': subscription,
            'tenant_url': subscription.tenant_url,
        }
        return request.render('saas_portal.activation_complete', values)

    @http.route('/saas/activation/status/<int:subscription_id>/json', type='json', auth='user')
    def activation_status_json(self, subscription_id, **kwargs):
        """AJAX endpoint for polling provisioning status"""
        subscription = request.env['saas.subscription'].sudo().browse(subscription_id)

        if not subscription.exists():
            return {'state': 'error', 'message': 'Subscription not found'}

        return {
            'state': subscription.state,
            'tenant_url': subscription.tenant_url,
            'is_ready': subscription.state == 'active',
            'is_failed': subscription.state == 'provisioning_failed',
        }