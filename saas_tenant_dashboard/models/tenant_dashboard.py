import json
import logging
from datetime import date

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

_GB = 1024.0 ** 3


class SaasTenantDashboard(models.TransientModel):
    _name = 'saas.tenant.dashboard'
    _description = 'Tenant Subscription & Usage Dashboard'
    # Use the plan name as the record label so the breadcrumb reads e.g.
    # "Professional" instead of the raw "saas.tenant.dashboard,1".
    _rec_name = 'package_name'

    # ── Plan (from the master snapshot) ──
    package_name = fields.Char(string='Plan', readonly=True)
    billing_plan = fields.Char(string='Billing', readonly=True)
    status = fields.Char(string='Status', readonly=True)
    is_trial = fields.Boolean(string='Free Trial', readonly=True)
    price_display = fields.Char(string='Rate', readonly=True)
    start_date = fields.Date(string='Started', readonly=True)
    renewal_date = fields.Date(string='Renews / Next Invoice', readonly=True)
    days_left = fields.Integer(string='Days Left', readonly=True)
    subscription_ref = fields.Char(string='Reference', readonly=True)

    # ── Storage (computed live in-tenant) ──
    storage_used_gb = fields.Float(string='Storage Used (GB)', readonly=True, digits=(16, 2))
    storage_limit_gb = fields.Float(string='Storage Limit (GB)', readonly=True, digits=(16, 2))
    storage_used_pct = fields.Float(string='Storage Used %', readonly=True)
    storage_display = fields.Char(string='Storage', readonly=True)
    storage_unlimited = fields.Boolean(string='Unlimited Storage', readonly=True)
    # 'ok' | 'warn' | 'full' — drives the banner colour
    storage_level = fields.Selection(
        [('ok', 'OK'), ('warn', 'Near limit'), ('full', 'Over limit')],
        string='Storage Level', readonly=True, default='ok')

    # ── Users (computed live in-tenant) ──
    users_used = fields.Integer(string='Users', readonly=True)
    user_limit = fields.Integer(string='User Limit', readonly=True)
    users_display = fields.Char(string='Users', readonly=True)

    # ── Apps ──
    app_count = fields.Integer(string='Active Apps', readonly=True)
    apps_display = fields.Char(string='Installed Apps', readonly=True)

    # ── Portal deep-links ──
    manage_url = fields.Char(readonly=True)
    upgrade_url = fields.Char(readonly=True)

    # ------------------------------------------------------------------
    def _read_snapshot(self):
        raw = self.env['ir.config_parameter'].sudo().get_param('saas.subscription_info')
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            _logger.warning("Could not parse saas.subscription_info")
            return {}

    def _live_storage_gb(self):
        """Approximate storage used = database size + file attachments, in GB."""
        used = 0
        try:
            self.env.cr.execute("SELECT pg_database_size(current_database())")
            used += self.env.cr.fetchone()[0] or 0
        except Exception as e:
            _logger.warning("pg_database_size failed: %s", e)
        try:
            self.env.cr.execute("SELECT COALESCE(SUM(file_size), 0) FROM ir_attachment")
            used += self.env.cr.fetchone()[0] or 0
        except Exception as e:
            _logger.warning("attachment size sum failed: %s", e)
        return used / _GB

    def _live_user_count(self):
        return self.env['res.users'].sudo().search_count([
            ('share', '=', False), ('active', '=', True),
        ])

    def _installed_apps(self):
        mods = self.env['ir.module.module'].sudo().search([
            ('state', '=', 'installed'), ('application', '=', True),
        ])
        return sorted(m.shortdesc or m.name for m in mods)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        info = self._read_snapshot()
        cur = info.get('currency') or '৳'

        # ---- Plan ----
        res['package_name'] = info.get('package_name') or 'Your Plan'
        res['billing_plan'] = info.get('billing_plan_label') or ''
        state = (info.get('state') or '').replace('_', ' ').title()
        res['status'] = 'Free Trial' if info.get('is_trial') else (state or 'Active')
        res['is_trial'] = bool(info.get('is_trial'))
        res['subscription_ref'] = info.get('subscription_ref') or ''
        monthly = info.get('monthly_price') or 0.0
        if monthly:
            res['price_display'] = '%s%s/month' % (cur, '{:,.2f}'.format(monthly))

        def _parse_date(key):
            v = info.get(key)
            try:
                return fields.Date.to_date(v) if v else False
            except Exception:
                return False

        res['start_date'] = _parse_date('date_start')
        # Trials show trial end; paid shows next invoice date.
        renew = _parse_date('trial_end_date') if info.get('is_trial') else _parse_date('date_next_invoice')
        res['renewal_date'] = renew
        res['days_left'] = max(0, (renew - date.today()).days) if renew else 0

        # ---- Storage (live) ----
        used_gb = self._live_storage_gb()
        limit_gb = float(info.get('storage_limit_gb') or 0.0)
        res['storage_used_gb'] = round(used_gb, 2)
        res['storage_limit_gb'] = limit_gb
        if limit_gb > 0:
            pct = min(100.0, used_gb / limit_gb * 100.0)
            res['storage_used_pct'] = round(pct, 1)
            res['storage_unlimited'] = False
            res['storage_display'] = '%s GB of %s GB used' % (
                '{:,.2f}'.format(used_gb), '{:,.0f}'.format(limit_gb))
            res['storage_level'] = 'full' if pct >= 100 else ('warn' if pct >= 80 else 'ok')
        else:
            res['storage_used_pct'] = 0.0
            res['storage_unlimited'] = True
            res['storage_display'] = '%s GB used (unlimited plan)' % '{:,.2f}'.format(used_gb)
            res['storage_level'] = 'ok'

        # ---- Users (live) ----
        users_used = self._live_user_count()
        user_limit = int(info.get('user_limit') or 0)
        res['users_used'] = users_used
        res['user_limit'] = user_limit
        res['users_display'] = ('%d of %d users' % (users_used, user_limit)) if user_limit \
            else ('%d users (unlimited)' % users_used)

        # ---- Apps ----
        apps = self._installed_apps()
        res['app_count'] = len(apps)
        res['apps_display'] = ', '.join(apps)

        # ---- Links ----
        res['manage_url'] = info.get('manage_url') or info.get('portal_base_url') or ''
        res['upgrade_url'] = info.get('upgrade_url') or info.get('manage_url') or ''
        return res

    # ------------------------------------------------------------------
    def _open_url(self, url):
        if not url:
            return {'type': 'ir.actions.act_window_close'}
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}

    def action_manage_plan(self):
        self.ensure_one()
        return self._open_url(self.manage_url)

    def action_upgrade(self):
        self.ensure_one()
        return self._open_url(self.upgrade_url)

    @api.model
    def action_open_dashboard(self):
        """Open a fresh dashboard record (values recomputed on each open)."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'My Subscription',
            'res_model': 'saas.tenant.dashboard',
            'view_mode': 'form',
            'target': 'current',
            'context': {},
        }
