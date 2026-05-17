from odoo import http
from odoo.http import request
from odoo.exceptions import UserError
from werkzeug.exceptions import NotFound
import json
import logging
from markupsafe import Markup

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

        # Build duration discount data for the Customize Your Plan section
        duration_data = {}
        for pkg in packages:
            tiers = pkg.duration_discount_ids.filtered('is_active').sorted('sequence')
            duration_data[pkg.id] = [
                {
                    'duration_months': t.duration_months,
                    'label': t.label,
                    'discount_percent': t.discount_percent,
                }
                for t in tiers
            ]

        values = {
            'packages': packages,
            'popular_packages': popular_packages,
            'billing_cycle': billing_cycle,
            'page_name': 'packages',
            'error': kwargs.get('error'),
            'duration_data': Markup(json.dumps(duration_data)),
            'currency_symbol': request.env.company.currency_id.symbol or '\u09f3',
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

    @http.route('/saas/pricing/calculate', type='json', auth='public', methods=['POST'])
    def pricing_calculate(self, **kwargs):
        """Real-time pricing calculator for the Customize Your Plan section.

        Accepts:
            package_id (int): selected package
            duration_months (int): commitment duration

        Returns:
            Full pricing breakdown dict
        """
        package_id = int(kwargs.get('package_id', 0))
        duration_months = int(kwargs.get('duration_months', 1))

        if not package_id:
            return {'error': 'No package selected'}

        package = request.env['saas.package'].sudo().browse(package_id)
        if not package.exists() or not package.active:
            return {'error': 'Package not found'}

        pricing = package.get_duration_pricing(duration_months)
        return pricing

    @http.route('/saas/signup', type='http', auth='public', website=True, methods=['GET', 'POST'])
    def signup_form(self, package_id=None, **kwargs):
        """Signup / login gate for new subscriptions.
        
        If the user is already logged in (not public), skip signup entirely
        and create the subscription + redirect to checkout.
        """
        package_id = package_id or kwargs.get('package_id')
        duration = int(kwargs.get('duration', 1))
        billing_cycle = kwargs.get('billing_cycle', 'monthly')

        if not package_id:
            return request.redirect('/saas/packages?error=No package selected')

        package = request.env['saas.package'].sudo().browse(int(package_id))
        if not package.exists() or not package.active:
            return request.redirect('/saas/packages?error=Package not found')

        # ── If user is already logged in, skip signup ──
        if request.env.user and not request.env.user._is_public():
            return self._create_subscription_and_checkout(
                request.env.user.partner_id, package, billing_cycle, duration
            )

        if request.httprequest.method == 'GET':
            values = {
                'package': package,
                'billing_cycle': billing_cycle,
                'duration': duration,
                'error': kwargs.get('error'),
            }
            return request.render('saas_portal.signup_form', values)

        else:
            # Process signup (POST)
            name = kwargs.get('name')
            email = kwargs.get('email')
            password = kwargs.get('password')
            confirm_password = kwargs.get('confirm_password')
            company_name = kwargs.get('company_name')
            phone = kwargs.get('phone')
            duration = int(kwargs.get('duration', 1))

            # Validation
            error = None
            if not name or not email or not password:
                error = 'All required fields must be filled.'
            elif password != confirm_password:
                error = 'Passwords do not match.'
            elif len(password) < 8:
                error = 'Password must be at least 8 characters.'

            # Check if user already exists
            existing_user = request.env['res.users'].sudo().search(
                [('login', '=', email)], limit=1
            )
            if existing_user:
                error = (
                    'An account with this email already exists. '
                    'Please log in first, then select your package.'
                )

            if error:
                return request.redirect(
                    f'/saas/signup?package_id={package_id}'
                    f'&duration={duration}&error={error}'
                )

            # Create partner and user
            try:
                partner = request.env['res.partner'].sudo().create({
                    'name': name,
                    'email': email,
                    'phone': phone,
                    'company_name': company_name or name,
                    'is_company': True if company_name else False,
                })

                user = request.env['res.users'].sudo().with_context(
                    no_reset_password=True,
                ).create({
                    'name': name,
                    'login': email,
                    'password': password,
                    'partner_id': partner.id,
                    'email': email,
                    'groups_id': [(6, 0, [
                        request.env.ref('base.group_portal').id,
                    ])],
                })

                # Flush before authenticate
                request.env.cr.flush()

                # Login the user
                try:
                    db_name = request.db or request.env.cr.dbname
                    request.session.authenticate(db_name, email, password)
                except Exception as auth_err:
                    _logger.warning(
                        f"Auto-login failed (non-fatal): {auth_err}")

                # Create subscription and redirect to checkout
                return self._create_subscription_and_checkout(
                    partner, package, billing_cycle, duration
                )

            except Exception as e:
                _logger.error(f"Signup error: {e}", exc_info=True)
                return request.redirect(
                    f'/saas/signup?package_id={package_id}'
                    f'&duration={duration}'
                    f'&error=Registration failed. Please try again.'
                )

    def _create_subscription_and_checkout(self, partner, package,
                                          billing_cycle, duration_months):
        """Shared helper: create subscription and redirect to checkout.
        
        Used by both the signup flow (new user) and the logged-in shortcut.
        """
        subscription = request.env['saas.subscription'].sudo().create({
            'partner_id': partner.id,
            'package_id': package.id,
            'billing_cycle': billing_cycle,
            'duration_months': duration_months,
            'state': 'draft',
        })

        subscription.action_confirm()

        request.session['pending_subscription_id'] = subscription.id
        return request.redirect('/saas/checkout')

    @http.route('/saas/checkout', type='http', auth='user', website=True)
    def checkout_page(self, **kwargs):
        """Checkout page with order summary and payment.
        
        Uses get_duration_pricing() so prices match the packages page.
        """
        subscription_id = request.session.get('pending_subscription_id')
        if not subscription_id:
            return request.redirect('/saas/packages?error=No subscription selected')

        subscription = request.env['saas.subscription'].sudo().browse(
            subscription_id
        )
        if not subscription.exists() or subscription.state != 'pending':
            return request.redirect('/saas/packages?error=Invalid subscription')

        # Get points balance
        points_balance = 0
        points_discount = 0
        redeemed_points = 0

        if request.env.user.partner_id:
            points_record = request.env['saas.partner.points'].sudo().search([
                ('partner_id', '=', request.env.user.partner_id.id)
            ], limit=1)
            points_balance = points_record.balance if points_record else 0

        # ── Duration-aware pricing ──
        duration = subscription.duration_months or 1
        pricing = subscription.package_id.get_duration_pricing(duration)
        base_price = pricing['base_price']
        discount_pct = pricing.get('discount_percent', 0)
        discount_amt = pricing.get('discount_amount', 0)
        monthly_price = pricing['monthly_price']
        total_price = pricing['total_price']
        setup_fee = subscription.package_id.setup_fee
        order_total = total_price + setup_fee
        currency_symbol = pricing.get(
            'currency_symbol',
            request.env.company.currency_id.symbol or '৳'
        )

        # Apply points if requested
        if kwargs.get('redeem_points'):
            points_to_redeem = int(kwargs.get('redeem_points', 0))
            if points_to_redeem <= points_balance:
                config = request.env['saas.points.config'].get_config()
                value_per_point = config['points_value_per_unit']
                max_points = (
                    int(order_total / value_per_point)
                    if value_per_point > 0 else 0
                )
                redeemed_points = min(points_to_redeem, max_points)
                points_discount = redeemed_points * value_per_point
                request.session['redeemed_points'] = redeemed_points

        total = max(0, order_total - points_discount)

        values = {
            'subscription': subscription,
            'base_price': base_price,
            'discount_pct': discount_pct,
            'discount_amt': discount_amt,
            'monthly_price': monthly_price,
            'duration': duration,
            'subtotal': total_price,
            'setup_fee': setup_fee,
            'points_discount': points_discount,
            'total': total,
            'points_balance': points_balance,
            'redeemed_points': redeemed_points,
            'currency_symbol': currency_symbol,
            'error': kwargs.get('error'),
        }
        return request.render('saas_portal.checkout_page', values)

    @http.route('/saas/checkout/pay', type='http', auth='user', methods=['POST'], website=True)
    def checkout_pay(self, **kwargs):
        """Process payment and redirect to SSLCommerz gateway"""
        subscription_id = request.session.get('pending_subscription_id')
        if not subscription_id:
            return request.redirect('/saas/packages?error=No subscription selected')

        subscription = request.env['saas.subscription'].sudo().browse(subscription_id)

        # ── Duration-aware pricing ──
        duration = subscription.duration_months or 1
        pricing = subscription.package_id.get_duration_pricing(duration)
        total_price = pricing['total_price']
        setup_fee = subscription.package_id.setup_fee
        order_total = total_price + setup_fee
        points_discount = 0

        # Handle points redemption
        if kwargs.get('redeem_points_checkbox'):
            points_to_redeem = int(kwargs.get('redeem_points_amount', 0))
            if points_to_redeem > 0:
                try:
                    config = request.env['saas.points.config'].get_config()
                    value_per_point = config['points_value_per_unit']

                    request.env['saas.points.transaction'].sudo().redeem_points(
                        subscription.partner_id.id,
                        points_to_redeem,
                        subscription_id=subscription.id,
                        order_total=order_total,
                    )
                    points_discount = points_to_redeem * value_per_point
                except Exception as e:
                    return request.redirect(f'/saas/checkout?error={str(e)}')

        total = max(0, order_total - points_discount)
        if total <= 0:
            subscription.action_activate()
            return request.redirect(f'/saas/activation/{subscription.id}')

        # Create SSLCommerz payment session
        try:
            # Use web.base.url (set in System Parameters) instead of
            # request.httprequest.url_root — the latter is unreliable
            # behind a Docker/Nginx reverse proxy and often returns
            # http://localhost:8069 instead of the public domain.
            base_url = request.env['ir.config_parameter'].sudo().get_param(
                'web.base.url', request.httprequest.url_root
            ).rstrip('/')

            _logger.info(
                f"Initiating SSLCommerz payment: sub={subscription.name}, "
                f"amount={total}, base_url={base_url}"
            )

            gateway_url = subscription.create_sslcommerz_session(
                return_url=base_url,
                purpose='checkout',
                amount_override=total,
            )

            if gateway_url:
                return request.redirect(gateway_url, local=False)
            else:
                return request.redirect('/saas/checkout?error=Payment gateway returned no URL. Check SSLCommerz configuration.')

        except Exception as e:
            _logger.error(f"Payment error: {e}", exc_info=True)
            return request.redirect(f'/saas/checkout?error={str(e)}')

    @http.route('/saas/activation/<int:subscription_id>', type='http', auth='public', website=True)
    def activation_status(self, subscription_id, **kwargs):
        """Show provisioning status with AJAX polling.
        
        auth='public' because the user's session may have been lost
        during the SSLCommerz redirect (external domain resets cookies).
        """
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

    @http.route('/saas/activation/complete/<int:subscription_id>', type='http', auth='public', website=True)
    def activation_complete(self, subscription_id, **kwargs):
        """Activation complete page with tenant credentials"""
        subscription = request.env['saas.subscription'].sudo().browse(subscription_id)

        values = {
            'subscription': subscription,
            'tenant_url': subscription.tenant_url,
        }
        return request.render('saas_portal.activation_complete', values)

    @http.route('/saas/activation/status/<int:subscription_id>/json', type='http', auth='public', csrf=False)
    def activation_status_json(self, subscription_id, **kwargs):
        """AJAX endpoint for polling provisioning status.
        
        Uses type='http' (not 'json') because the JavaScript polling code
        sends plain GET requests, not JSON-RPC POST.
        """
        subscription = request.env['saas.subscription'].sudo().browse(subscription_id)

        if not subscription.exists():
            data = {'state': 'error', 'message': 'Subscription not found'}
        else:
            data = {
                'state': subscription.state,
                'tenant_url': subscription.tenant_url or '',
                'is_ready': subscription.state == 'active',
                'is_failed': subscription.state == 'provisioning_failed',
            }

        import json
        return request.make_response(
            json.dumps(data),
            headers=[('Content-Type', 'application/json')]
        )
