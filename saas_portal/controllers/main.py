from odoo import http, fields
from odoo.http import request
from odoo.exceptions import UserError
from werkzeug.exceptions import NotFound
from datetime import date, timedelta
import logging
import re
import time
from urllib.parse import quote

_logger = logging.getLogger(__name__)


class SaasPublicPortal(http.Controller):

    # ── Company short-form (workspace handle) helpers ──
    # The short form is 3–5 letters/digits and becomes the tenant subdomain.
    SHORTNAME_RE = re.compile(r'^[a-z0-9]{3,5}$')

    # ── Public-signup abuse guards ──
    # Signature of the junk hammering /saas/signup: shell / command and
    # template-injection payloads, XSS probes, and known scanner beacons —
    # none of which ever appear in a real name or company field, so a match
    # means we drop the request.
    _ABUSE_SIGNATURE_RE = re.compile(
        r'(\$\{|\$\(|`|\|\||<\s*script|onerror\s*=|javascript:|response\.write|'
        r'bxss\.me|nslookup\b|\bcurl\b|\bwget\b|\bping\s+-)',
        re.IGNORECASE)
    # Per-worker, per-IP sliding window — a blunt speed-bump against bursts,
    # NOT a substitute for edge/CDN rate-limiting. (Behind a reverse proxy,
    # enable proxy_mode so remote_addr is the real client IP, not the proxy.)
    _SIGNUP_HITS = {}
    _SIGNUP_MAX = 6
    _SIGNUP_WINDOW = 600  # seconds

    def _normalize_shortname(self, raw):
        """Return ``(code, error)``. ``code`` is lowercased; valid iff it is
        3–5 letters or digits. ``error`` is None when valid."""
        code = (raw or '').strip().lower()
        if not code:
            return '', 'Company Short Form is required (it becomes your workspace address).'
        if not self.SHORTNAME_RE.match(code):
            return code, ('Company Short Form must be 3–5 letters or digits '
                          '(no spaces or symbols).')
        return code, None

    def _suggest_shortname(self, subscription):
        """Best-effort 3–5 char lowercase suggestion for the short form: the one
        chosen at signup if it already fits, else a trimmed company-name slug."""
        code = re.sub(r'[^a-z0-9]', '', (subscription.tenant_shortname or '').lower())
        if not (3 <= len(code) <= 5):
            src = subscription.partner_id.company_name or subscription.partner_id.name or ''
            code = re.sub(r'[^a-z0-9]', '', src.lower())[:5]
        return code if 3 <= len(code) <= 5 else ''

    def _customer_has_active_trial(self, partner, exclude_id=None):
        """One active free trial per customer — True if this partner already has
        a live (non-canceled) trial subscription."""
        return bool(request.env['saas.subscription'].sudo().search_count([
            ('partner_id', '=', partner.id),
            ('is_trial', '=', True),
            ('state', 'not in', ('canceled', 'rejected')),
            ('id', '!=', exclude_id or 0),
        ]))

    def _get_customer_primary_subscription(self, partner):
        """The customer's main live workspace = their highest-tier ACTIVE
        subscription. Used to enforce ONE active workspace per customer: a repeat
        purchase upgrades this instance (if higher tier) rather than spinning up a
        second tenant. Returns an empty recordset when the customer has none."""
        subs = request.env['saas.subscription'].sudo().search([
            ('partner_id', '=', partner.id),
            ('state', '=', 'active'),
        ])
        return max(subs, key=lambda s: s.package_id.tier_level or 0) if subs else subs

    def _valid_duration(self, package, duration):
        """Clamp the requested commitment duration to a legitimate value: one of
        the package's active duration-discount tiers, or 1 (monthly). Blocks
        crafted durations — e.g. 0 (→ ₹0 total → free tenant) or 999 (bogus price)."""
        try:
            duration = int(duration)
        except (TypeError, ValueError):
            return 1
        valid = set(package.duration_discount_ids.filtered('is_active').mapped('duration_months')) | {1}
        return duration if duration in valid else 1

    def _signup_rate_limited(self, ip):
        """True once this IP exceeds the signup-POST budget for the window.
        Prunes stale timestamps as it goes. Per worker process (see note on
        ``_SIGNUP_HITS``)."""
        if not ip:
            return False
        now = time.time()
        hits = [t for t in self._SIGNUP_HITS.get(ip, []) if now - t < self._SIGNUP_WINDOW]
        hits.append(now)
        self._SIGNUP_HITS[ip] = hits
        if len(self._SIGNUP_HITS) > 2048:   # keep the map from growing unbounded
            type(self)._SIGNUP_HITS = {
                k: v for k, v in self._SIGNUP_HITS.items()
                if v and now - v[-1] < self._SIGNUP_WINDOW
            }
        return len(hits) > self._SIGNUP_MAX

    def _looks_like_attack(self, *values):
        """True if any supplied free-text field carries a scanner/injection
        signature (see ``_ABUSE_SIGNATURE_RE``)."""
        return any(v and self._ABUSE_SIGNATURE_RE.search(v) for v in values)

    @http.route('/saas/packages', type='http', auth='public', website=True)
    def package_listing(self, **kwargs):
        """Public landing page showing all active packages.

        The pricing UI itself lives in the reusable
        ``saas_portal.pricing_block`` template, which fetches its own packages
        and duration data so it can also be embedded elsewhere (e.g. the
        website homepage). This route only renders the page wrapper and passes
        through any error message.
        """
        return request.render('saas_portal.package_listing', {
            'error': kwargs.get('error'),
        })

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

        # Validate the committed duration against this package's configured tiers
        # (guards crafted ?duration= values, e.g. 0 → free, or 999 → bogus price).
        duration = self._valid_duration(package, duration)

        # Free trial requested? (honoured only if this package offers one)
        trial = bool(kwargs.get('trial')) and package.trial_enabled

        # ── If user is already logged in, skip signup ──
        if request.env.user and not request.env.user._is_public():
            partner = request.env.user.partner_id

            # One active workspace per customer. A repeat purchase must NOT create
            # a second tenant — route it into the upgrade flow (higher tier) or
            # block it (same/lower tier — no downgrades, no duplicate plans).
            primary = self._get_customer_primary_subscription(partner)
            if primary:
                if primary.is_trial:
                    return request.redirect('/my/subscriptions?error=' + quote(
                        'You have an active free trial. Subscribe to it (or let it '
                        'end) before purchasing another plan.'))
                if package.tier_level > primary.package_id.tier_level:
                    # Higher tier → upgrade the existing instance in place.
                    return request.redirect(
                        '/my/subscriptions/%s/upgrade?message=%s' % (
                            primary.id,
                            quote('You already have an active %s plan — choose your '
                                  'upgrade below. Your current workspace is kept and '
                                  'simply gains the new plan\'s apps.'
                                  % primary.package_id.name)))
                # Same or lower tier → not allowed.
                return request.redirect('/my/subscriptions?error=' + quote(
                    'You already have an active %s plan. Downgrades and duplicate '
                    'plans aren\'t supported — you can upgrade it from here.'
                    % primary.package_id.name))

            if trial and self._customer_has_active_trial(partner):
                return request.redirect('/saas/packages?error=You already have an active free trial.')
            return self._create_subscription_and_checkout(
                partner, package, billing_cycle, duration,
                is_trial=trial, trial_days=(package.trial_days if trial else 0),
                terms_accepted=bool(kwargs.get('consent')),
            )

        if request.httprequest.method == 'GET':
            values = {
                'package': package,
                'billing_cycle': billing_cycle,
                'duration': duration,
                'trial': trial,
                # Pre-tick the Terms box when the visitor accepted via the modal gate.
                'terms_prechecked': bool(kwargs.get('consent')),
                'error': kwargs.get('error'),
                # Shown in the form as the live example workspace address.
                'base_domain': request.env['ir.config_parameter'].sudo().get_param(
                    'saas.domain_base', 'perfecthr.net'
                ),
            }
            return request.render('saas_portal.signup_form', values)

        else:
            # Process signup (POST)

            # ── Abuse guards: drop obvious bot / scanner submissions BEFORE we
            #    create any partner or user. Silent redirects (no hint at what
            #    tripped). See the junk-signup history on this endpoint. ──
            client_ip = request.httprequest.remote_addr or ''
            if self._signup_rate_limited(client_ip):
                _logger.warning("Signup rate-limited: ip=%s", client_ip)
                return request.redirect('/saas/packages')
            if (kwargs.get('company_fax') or '').strip():   # honeypot: humans never fill it
                _logger.warning("Signup honeypot tripped: ip=%s", client_ip)
                return request.redirect('/')

            name = (kwargs.get('name') or '').strip()
            email = (kwargs.get('email') or '').strip()
            password = kwargs.get('password') or ''
            confirm_password = kwargs.get('confirm_password') or ''
            company_name = (kwargs.get('company_name') or '').strip()
            company_shortname = (kwargs.get('company_shortname') or '').strip()
            phone = (kwargs.get('phone') or '').strip()
            duration = self._valid_duration(package, kwargs.get('duration', 1))

            # Scanner / injection payloads in any free-text field → drop silently.
            if self._looks_like_attack(name, company_name, company_shortname, phone, email):
                _logger.warning(
                    "Signup payload signature dropped: ip=%s name=%r company=%r",
                    client_ip, name[:40], company_name[:40])
                return request.redirect('/')

            # Terms & Conditions must be accepted (server-side backstop for the
            # website T&C gate). The public form's checkbox is `required`, but a
            # direct POST can omit it — so enforce it here too.
            terms_ok = bool(kwargs.get('terms_accepted'))

            # Validate & normalize the company short form (3–5 letters/digits).
            shortcode, shortcode_error = self._normalize_shortname(company_shortname)

            # Validation
            error = None
            if not name or not email or not password:
                error = 'All required fields must be filled.'
            elif not company_name:
                error = 'Company Name is required.'
            elif shortcode_error:
                error = shortcode_error
            elif password != confirm_password:
                error = 'Passwords do not match.'
            elif len(password) < 8:
                error = 'Password must be at least 8 characters.'
            elif not terms_ok:
                error = 'You must accept the Terms and Conditions to continue.'

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
                    f'&duration={duration}'
                    f"&trial={'1' if trial else ''}"
                    f"&consent={'1' if kwargs.get('consent') else ''}"
                    f'&error={quote(error)}'
                )

            # Create partner and user
            try:
                partner = request.env['res.partner'].sudo().create({
                    'name': name,
                    'email': email,
                    'phone': phone,
                    # Full company name (used for branding). The separate
                    # short form drives the tenant subdomain/database name.
                    'company_name': company_name,
                    'is_company': False,
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

                # Flush so the new user/partner are persisted before we
                # create the subscription.
                request.env.cr.flush()

                # Create the pending subscription now and stash its id in the
                # session so checkout can pick it up after the customer signs
                # in. New customers must authenticate before checkout, so we
                # send them to a confirmation page that clearly prompts sign-in
                # (instead of dropping them onto a bare login screen).
                self._create_pending_subscription(
                    partner, package, billing_cycle, duration,
                    shortname=shortcode,
                    is_trial=trial, trial_days=(package.trial_days if trial else 0),
                    terms_accepted=True,
                )
                return request.redirect(
                    '/saas/account-created?email=' + quote(email)
                )

            except Exception as e:
                _logger.error(f"Signup error: {e}", exc_info=True)
                return request.redirect(
                    f'/saas/signup?package_id={package_id}'
                    f'&duration={duration}'
                    f"&trial={'1' if trial else ''}"
                    f"&consent={'1' if kwargs.get('consent') else ''}"
                    f'&error={quote("Registration failed. Please try again.")}'
                )

    def _create_pending_subscription(self, partner, package,
                                     billing_cycle, duration_months,
                                     shortname=None, is_trial=False, trial_days=0,
                                     terms_accepted=False):
        """Create a draft subscription, confirm it, and stash its id in the
        session so checkout can pick it up (surviving a sign-in redirect).

        ``shortname`` is the customer-chosen company short form that drives the
        tenant subdomain. When None (e.g. the logged-in shortcut) the
        provisioner falls back to the partner's company name. When ``is_trial``
        the subscription is flagged as a free trial ending in ``trial_days``.
        """
        vals = {
            'partner_id': partner.id,
            'package_id': package.id,
            'billing_cycle': billing_cycle,
            'duration_months': duration_months,
            'tenant_shortname': (shortname or '').strip() or False,
            'state': 'draft',
        }
        if is_trial:
            vals['is_trial'] = True
            vals['trial_end_date'] = date.today() + timedelta(days=trial_days or 14)
        if terms_accepted:
            vals['terms_accepted'] = True
            vals['terms_accepted_date'] = fields.Datetime.now()
        subscription = request.env['saas.subscription'].sudo().create(vals)
        subscription.action_confirm()
        request.session['pending_subscription_id'] = subscription.id
        return subscription

    def _create_subscription_and_checkout(self, partner, package,
                                          billing_cycle, duration_months,
                                          shortname=None, is_trial=False, trial_days=0,
                                          terms_accepted=False):
        """Create the pending subscription and go straight to checkout.

        Used by the logged-in shortcut, where the customer is already
        authenticated so no sign-in step is needed.
        """
        self._create_pending_subscription(
            partner, package, billing_cycle, duration_months,
            shortname=shortname, is_trial=is_trial, trial_days=trial_days,
            terms_accepted=terms_accepted,
        )
        return request.redirect('/saas/checkout')

    @http.route('/saas/account-created', type='http', auth='public', website=True)
    def account_created(self, **kwargs):
        """Post-signup confirmation page that prompts the new customer to sign in.

        New accounts must authenticate before checkout. Instead of dropping the
        customer onto the bare Odoo login screen, we show a friendly
        confirmation with a clear 'Sign in to Continue' button that returns them
        to checkout after login (their pending subscription is held in session).
        """
        email = (kwargs.get('email') or '').strip()
        login_url = '/web/login?redirect=%2Fsaas%2Fcheckout'
        if email:
            login_url += '&login=' + quote(email)
        return request.render('saas_portal.account_created', {
            'email': email,
            'login_url': login_url,
        })

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

        # Prefill a valid 3–5 char short form: the one chosen at signup if it
        # already fits, else a trimmed slug of the company name. The customer
        # confirms/edits it here, so repeat/logged-in purchases also get one.
        suggested_shortname = self._suggest_shortname(subscription)

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
            'suggested_shortname': suggested_shortname,
            'is_trial': subscription.is_trial,
            'trial_days_left': subscription.trial_days_left,
            'base_domain': request.env['ir.config_parameter'].sudo().get_param(
                'saas.domain_base', 'perfecthr.net'),
        }
        return request.render('saas_portal.checkout_page', values)

    @http.route('/saas/checkout/pay', type='http', auth='user', methods=['POST'], website=True)
    def checkout_pay(self, **kwargs):
        """Process payment and redirect to SSLCommerz gateway"""
        subscription_id = request.session.get('pending_subscription_id')
        if not subscription_id:
            return request.redirect('/saas/packages?error=No subscription selected')

        subscription = request.env['saas.subscription'].sudo().browse(subscription_id)

        # Capture / confirm the tenant short form (3–5 letters/digits; drives the
        # tenant subdomain). Applies to every purchase, including repeat/logged-in
        # ones where the signup form was skipped.
        shortcode, shortcode_error = self._normalize_shortname(kwargs.get('company_shortname'))
        if shortcode_error:
            return request.redirect('/saas/checkout?error=' + quote(shortcode_error))
        subscription.write({'tenant_shortname': shortcode})

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

    @http.route('/saas/trial/start', type='http', auth='user', methods=['POST'], website=True)
    def trial_start(self, **kwargs):
        """Start a free trial — capture the short form, then activate the tenant
        WITHOUT any payment (reuses the normal provisioning path)."""
        subscription_id = request.session.get('pending_subscription_id')
        if not subscription_id:
            return request.redirect('/saas/packages?error=No subscription selected')

        subscription = request.env['saas.subscription'].sudo().browse(subscription_id)
        if not subscription.exists() or not subscription.is_trial:
            return request.redirect('/saas/checkout')

        # One active trial per customer.
        if self._customer_has_active_trial(subscription.partner_id, exclude_id=subscription.id):
            return request.redirect('/saas/packages?error=You already have an active free trial.')

        # Capture the workspace short form (subdomain).
        shortcode, shortcode_error = self._normalize_shortname(kwargs.get('company_shortname'))
        if shortcode_error:
            return request.redirect('/saas/checkout?error=' + quote(shortcode_error))
        subscription.write({'tenant_shortname': shortcode})

        # Activate → provisions the tenant immediately, no charge.
        subscription.action_activate()
        _logger.info("Free trial started for %s (ends %s)",
                     subscription.name, subscription.trial_end_date)
        return request.redirect(f'/saas/activation/{subscription.id}')

    @http.route('/saas/subscription/<int:subscription_id>/subscribe',
                type='http', auth='user', website=True)
    def trial_subscribe(self, subscription_id, **kwargs):
        """Convert a free trial to a paid subscription — send the customer to
        SSLCommerz for the plan price (no setup fee; the tenant already exists)."""
        subscription = request.env['saas.subscription'].sudo().browse(subscription_id)
        if not subscription.exists() or subscription.partner_id.id != request.env.user.partner_id.id:
            return request.redirect('/my/subscriptions')
        if not subscription.is_trial:
            return request.redirect(f'/my/subscriptions/{subscription_id}')

        duration = subscription.duration_months or 1
        pricing = subscription.package_id.get_duration_pricing(duration)
        amount = pricing['total_price']  # plan price only — no setup fee on convert
        try:
            base_url = request.env['ir.config_parameter'].sudo().get_param(
                'web.base.url', request.httprequest.url_root).rstrip('/')
            gateway_url = subscription.create_sslcommerz_session(
                return_url=base_url, purpose='checkout', amount_override=amount)
            if gateway_url:
                return request.redirect(gateway_url, local=False)
        except Exception as e:
            _logger.error(f"Trial conversion payment error: {e}", exc_info=True)
        return request.redirect(
            f'/my/subscriptions/{subscription_id}?error=Payment setup failed. Please try again.')

    @http.route('/saas/activation/<int:subscription_id>', type='http', auth='public', website=True)
    def activation_status(self, subscription_id, **kwargs):
        """Show provisioning status with AJAX polling.
        
        auth='public' because the user's session may have been lost
        during the SSLCommerz redirect (external domain resets cookies).
        """
        subscription = request.env['saas.subscription'].sudo().browse(subscription_id)

        if not subscription.exists():
            return request.redirect('/my/subscriptions?error=Subscription not found')

        # Only jump to the "Ready" page once the tenant is ACTUALLY provisioned
        # (tenant_url set). The subscription flips to 'active' at payment time —
        # well before the cron finishes building the instance — so redirecting on
        # state alone shows a premature "Ready". Until the URL is live we show the
        # live progress screen, which polls and advances on its own.
        if subscription.state == 'active' and subscription.tenant_url:
            return request.redirect(f'/saas/activation/complete/{subscription_id}')

        values = {
            'subscription': subscription,
            'tenant_url': subscription.tenant_url,
            'base_domain': request.env['ir.config_parameter'].sudo().get_param(
                'saas.domain_base', 'perfecthr.net'),
        }
        return request.render('saas_portal.activation_status', values)

    @http.route('/saas/activation/complete/<int:subscription_id>', type='http', auth='public', website=True)
    def activation_complete(self, subscription_id, **kwargs):
        """Activation complete page with tenant credentials.

        Guarded: if the tenant isn't actually provisioned yet (no tenant_url),
        send the customer back to the live progress screen instead of showing a
        premature "Ready".
        """
        subscription = request.env['saas.subscription'].sudo().browse(subscription_id)

        if not subscription.exists():
            return request.redirect('/my/subscriptions')
        if not subscription.tenant_url:
            return request.redirect(f'/saas/activation/{subscription_id}')

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
