from odoo import models, fields, api, _
from odoo.exceptions import UserError
import subprocess
import json
import logging
import secrets
import string
import base64
import os
import re
import shlex
import shutil
from datetime import datetime
from cryptography.fernet import Fernet

_logger = logging.getLogger(__name__)

class TenantProvisioner(models.Model):
    _name = 'tenant.provisioner'
    _description = 'Tenant Provisioning Engine'
    _rec_name = 'subscription_id'

    # Modules force-installed into EVERY tenant regardless of package, and always
    # kept in the allow-list so saas_tenant_guard never blocks them:
    #  - saas_tenant_guard     : locks the Apps menu / blocks unauthorized installs
    #  - saas_tenant_dashboard : the in-tenant "My Subscription & Usage" dashboard
    SYSTEM_TENANT_MODULES = {'saas_tenant_guard', 'saas_tenant_dashboard'}

    subscription_id = fields.Many2one('saas.subscription', string='Subscription', required=True)
    state = fields.Selection([
        ('pending', 'Pending'),
        ('provisioning', 'Provisioning'),
        ('completed', 'Completed'),
        ('failed', 'Failed')
    ], string='Provisioning State', default='pending')
    error_message = fields.Text(string='Error Message')
    attempt_count = fields.Integer(string='Attempt Count', default=0)
    started_at = fields.Datetime(string='Started At')
    completed_at = fields.Datetime(string='Completed At')

    @api.model
    def provision_tenant(self, subscription):
        """Main entry point for tenant provisioning"""
        provisioner = self.create({
            'subscription_id': subscription.id,
            'state': 'provisioning',
            'started_at': datetime.now(),
            'attempt_count': subscription.provision_attempts + 1
        })
        
        try:
            _logger.info(f"Starting tenant provisioning for subscription {subscription.name}")
            
            # Step 1: Generate tenant identifiers.
            # With dbfilter = ^%h$, the DB name must exactly equal the hostname so
            # Odoo routes the subdomain to the right database. We therefore use the
            # FULL FQDN as the database name (e.g. "abc123.dev.perfecthr.net").
            tenant_id = provisioner._generate_tenant_id(subscription)
            base_domain = provisioner._get_base_domain()
            tenant_domain = f"{tenant_id}.{base_domain}".lower()
            db_name = tenant_domain  # 1:1 hostname↔DB mapping for dbfilter
            
            # Step 2: Generate secure password
            db_password = provisioner._generate_secure_password()
            
            # Step 3: Create PostgreSQL database from template
            provisioner._create_tenant_db(db_name)
            
            # Step 3b: Copy filestore from template to tenant
            provisioner._copy_filestore(db_name)
            
            # Step 4: Configure Odoo database
            provisioner._configure_tenant_db(db_name, subscription, tenant_id)
            
            
            # Step 5: Install selected modules + system tenant modules
            # (tenant guard locks down the Apps menu; dashboard shows the
            # in-tenant subscription/usage view). Both go into every tenant.
            module_list = subscription.package_id.module_ids.mapped('name')
            install_list = list(set(module_list) | self.SYSTEM_TENANT_MODULES)
            provisioner._install_modules(db_name, install_list)

            # Step 5a: Store the allowed module list in the tenant DB
            # so saas_tenant_guard can enforce it at runtime.
            provisioner._store_allowed_modules(db_name, module_list)

            # Step 5a-ii: Push the subscription snapshot the dashboard reads.
            provisioner._store_subscription_snapshot(db_name, subscription)

            # Step 5a-iii: Push the AIHR AI-model entitlement for this package's tier.
            provisioner._store_ai_allowed_models(db_name, subscription)

            # Step 5a-iv: Seed AIHR connector config so the tenant reaches AIHR
            # with no manual setup (copied from this master's own config).
            provisioner._store_aihr_connector_config(db_name)

            # Step 5b: Restrict visible modules to package selection only
            provisioner._restrict_tenant_modules(db_name, module_list)
            
            # Step 5c: Regenerate web assets in tenant DB
            provisioner._regenerate_assets(db_name)
            
            # Step 6: Reset/configure tenant admin in tenant DB
            admin_password = provisioner._create_admin_user(db_name, subscription)
            
            # Step 7: Configure Nginx routing with wildcard SSL
            provisioner._update_nginx_config(tenant_id, tenant_domain, db_name)
            
            # Step 7b: Verify SSL for tenant subdomain
            provisioner._setup_tenant_ssl(tenant_domain)
            
            # Step 8: Update subscription record
            encrypted_password = provisioner._encrypt_password(db_password)
            subscription.write({
                'tenant_db_name': db_name,
                'tenant_db_password': encrypted_password,
                'tenant_url': f"https://{tenant_domain}",
                'provisioned_at': datetime.now(),
                'provision_attempts': provisioner.attempt_count,
                'state_reason': False
            })
            
            # Step 9: Send credentials email
            provisioner._send_credentials_email(subscription, tenant_domain, admin_password)
            
            provisioner.write({
                'state': 'completed',
                'completed_at': datetime.now()
            })
            
            _logger.info(f"Successfully provisioned tenant {db_name} for subscription {subscription.name}")
            return True
            
        except Exception as e:
            error_msg = str(e)
            _logger.error(f"Provisioning failed for subscription {subscription.name}: {error_msg}")
            
            provisioner.write({
                'state': 'failed',
                'error_message': error_msg
            })
            
            # Rollback: drop DB + clean Nginx vhost if either was created.
            try:
                _rb_db = db_name if 'db_name' in locals() else None
                _rb_dom = tenant_domain if 'tenant_domain' in locals() else None
                if _rb_db:
                    provisioner._rollback_tenant_db(_rb_db, tenant_domain=_rb_dom)
            except Exception as rb_err:
                _logger.error(f"Rollback raised: {rb_err}")
            
            old_state = subscription.state
            subscription.write({
                'state': 'provisioning_failed',
                'state_reason': f"Provisioning failed: {error_msg[:200]}"
            })
            subscription._log_state_change(old_state, 'provisioning_failed', error_msg)
            
            return False

    @api.model
    def _cron_retry_failed_provisioning(self):
        """Compatibility shim so the provisioning cron runs no matter which
        model its ir.cron record points at.

        The live scheduled action ("Retry Failed SaaS Provisioning", id 92) has
        its Model set to ``tenant.provisioner`` and runs
        ``model._cron_retry_failed_provisioning()`` — but the actual retry /
        provisioning logic lives on the ``subscription.cron`` abstract model.
        Delegating here makes the cron work WITHOUT needing a module upgrade to
        repoint the record, which is why the AttributeError kept returning after
        every restart.
        """
        return self.env['subscription.cron']._cron_retry_failed_provisioning()

    # ==================== PLAN UPGRADE — MODULE SYNC ====================

    @api.model
    def sync_tenant_modules(self, subscription):
        """Additively install the subscription's (upgraded) package apps into its
        EXISTING tenant database, then refresh the allow-list + Apps lockdown.

        Used by plan upgrades: no new tenant, no data loss — we only ADD the
        higher tier's modules to the running instance. Reuses the same helpers as
        first-time provisioning (Steps 5–5c). MUST refresh saas.allowed_modules or
        saas_tenant_guard keeps blocking the new apps.
        """
        db_name = subscription.tenant_db_name
        if not db_name:
            _logger.warning("sync_tenant_modules: subscription %s has no tenant DB",
                            subscription.name)
            return False

        provisioner = self.create({
            'subscription_id': subscription.id,
            'state': 'provisioning',
            'started_at': datetime.now(),
            'attempt_count': 0,
        })
        try:
            module_list = subscription.package_id.module_ids.mapped('name')
            install_list = list(set(module_list) | self.SYSTEM_TENANT_MODULES)
            installed = provisioner._get_available_modules(db_name)
            to_add = [m for m in install_list if m not in installed]

            _logger.info("Upgrade sync for %s: %d new module(s) into %s: %s",
                         subscription.name, len(to_add), db_name, to_add)
            if to_add:
                provisioner._install_modules(db_name, to_add)
            # Refresh the runtime allow-list + Apps restrictions for the new tier.
            provisioner._store_allowed_modules(db_name, module_list)
            provisioner._restrict_tenant_modules(db_name, module_list)
            provisioner._regenerate_assets(db_name)
            # Refresh the dashboard snapshot (new tier / quota / dates).
            provisioner._store_subscription_snapshot(db_name, subscription)
            # Refresh the AI-model entitlement for the new tier (upgrade adds models).
            provisioner._store_ai_allowed_models(db_name, subscription)
            # Ensure AIHR connectivity config is present (idempotent; token untouched).
            provisioner._store_aihr_connector_config(db_name)

            subscription.write({'module_sync_pending': False})
            provisioner.write({'state': 'completed', 'completed_at': datetime.now()})
            _logger.info("Upgrade sync complete for %s (%s)", subscription.name, db_name)
            return True
        except Exception as e:
            _logger.error("Upgrade module sync failed for %s: %s",
                          subscription.name, e, exc_info=True)
            provisioner.write({'state': 'failed', 'error_message': str(e)})
            # Leave module_sync_pending=True so the cron retries.
            return False

    @api.model
    def _cron_sync_tenant_modules(self):
        """Install pending upgrade apps into tenants. Runs in the cron worker
        (module install is minutes-long); triggered immediately after an upgrade
        and periodically as a backstop. Each tenant syncs in its own cursor so
        one long install can't roll back another's result."""
        import odoo
        dbname = self.env.cr.dbname
        subs = self.env['saas.subscription'].search([
            ('module_sync_pending', '=', True),
            ('tenant_db_name', '!=', False),
        ])
        for sub in subs:
            try:
                with odoo.registry(dbname).cursor() as sync_cr:
                    sync_env = api.Environment(sync_cr, self.env.uid, self.env.context)
                    sync_env['tenant.provisioner'].sudo().sync_tenant_modules(
                        sync_env['saas.subscription'].browse(sub.id))
                    sync_cr.commit()
            except Exception as e:
                _logger.error("Cron module-sync crashed for %s: %s",
                              sub.name, e, exc_info=True)

    # ==================== PROVISIONING STEPS ====================
    
    def _generate_tenant_id(self, subscription):
        """Derive the tenant's subdomain label from the customer's company name.

        The returned label is used BOTH as the subdomain (e.g. "diu" in
        diu.dev.perfecthr.net) and as part of the tenant database name, so it
        must be a valid DNS label AND a safe PostgreSQL identifier component:
          * lowercase
          * only a-z, 0-9 and hyphens
          * no leading/trailing hyphen, no consecutive hyphens
          * short enough that "<label>.<base_domain>" stays within
            PostgreSQL's 63-byte database-name limit

        Uniqueness is guaranteed by checking existing databases and appending
        a numeric suffix (-2, -3, …) on collision. If the company name yields
        no usable characters (or collides with a reserved subdomain), we fall
        back to a short hash so provisioning never fails on naming.
        """
        import hashlib

        base_domain = self._get_base_domain()

        # Prefer the customer's chosen short form; fall back to the full
        # company name, then the contact name.
        raw = (subscription.tenant_shortname
               or subscription.partner_id.company_name
               or subscription.partner_id.name or '')

        # Slugify to a DNS-safe label.
        slug = raw.strip().lower()
        slug = re.sub(r'[^a-z0-9]+', '-', slug)      # non-alphanumerics -> hyphen
        slug = re.sub(r'-{2,}', '-', slug).strip('-')

        # Bound the length: leave room for ".<base_domain>" within 63 bytes,
        # and cap at 40 for tidy URLs.
        max_label = max(3, 63 - len(base_domain) - 1)
        slug = slug[:min(40, max_label)].strip('-')

        # Never allow empty or reserved/infrastructure subdomains.
        reserved = {
            'www', 'app', 'api', 'admin', 'mail', 'smtp', 'ns', 'ns1', 'ns2',
            'saas', 'saas-template', 'saas_template', 'template', 'postgres',
            'pgadmin', 'static', 'assets', 'cdn',
            base_domain.split('.')[0],
        }
        if not slug or slug in reserved:
            slug = 'tenant-' + hashlib.md5(str(subscription.id).encode()).hexdigest()[:8]

        # Ensure the resulting database / subdomain is globally unique.
        candidate = slug
        suffix = 2
        while self._tenant_db_exists(f"{candidate}.{base_domain}".lower()):
            tail = f"-{suffix}"
            trimmed = slug[:max(3, min(40, max_label) - len(tail))].strip('-')
            candidate = f"{trimmed}{tail}"
            suffix += 1
            if suffix > 999:  # pathological safety valve
                candidate = 'tenant-' + hashlib.md5(
                    f"{subscription.id}-{datetime.now().timestamp()}".encode()
                ).hexdigest()[:10]
                break

        _logger.info(
            f"Derived tenant subdomain '{candidate}' from company name "
            f"'{raw}' for subscription {subscription.name}"
        )
        return candidate

    def _tenant_db_exists(self, db_name):
        """Return True if a PostgreSQL database with this exact name exists.

        Used by _generate_tenant_id to guarantee subdomain/database uniqueness
        before provisioning begins.
        """
        if not db_name or not re.match(r'^[a-z0-9_.-]+$', db_name):
            # Treat malformed names as 'taken' so the caller regenerates.
            return True
        try:
            check = subprocess.run(
                ['psql', '-X', '-tA', '-d', 'postgres', '-c',
                 f"SELECT 1 FROM pg_database WHERE datname = '{db_name}';"],
                capture_output=True, text=True, timeout=15
            )
        except Exception as e:
            _logger.warning(f"DB existence check failed for {db_name}: {e}")
            return False
        return check.returncode == 0 and check.stdout.strip() == '1'

    def _generate_secure_password(self):
        """Generate secure random password"""
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        password = ''.join(secrets.choice(alphabet) for _ in range(24))
        return password

    def _create_tenant_db(self, db_name):
        """Create PostgreSQL database from template (no shell, no injection risk)."""
        template_db = self._get_template_db_name()

        # Tenant identifiers are lower-case hex + domain; validate strictly.
        if not re.match(r'^[a-z0-9_.-]+$', db_name):
            raise Exception(f"Invalid tenant DB name: {db_name}")
        # PostgreSQL limits identifiers to 63 bytes.
        if len(db_name) > 63:
            raise Exception(
                f"Tenant DB name too long ({len(db_name)} > 63). "
                "Shorten saas.domain_base."
            )
        if not re.match(r'^[a-zA-Z0-9_]+$', template_db):
            raise Exception(f"Invalid template DB name: {template_db}")

        # Check existence via psql -tA (no parsing of formatted output).
        check = subprocess.run(
            ['psql', '-X', '-tA', '-d', 'postgres', '-c',
             f"SELECT 1 FROM pg_database WHERE datname = '{db_name}';"],
            capture_output=True, text=True, timeout=15
        )
        if check.returncode == 0 and check.stdout.strip() == '1':
            raise Exception(f"Database {db_name} already exists")

        # CRITICAL: Terminate ALL connections to the template database.
        # PostgreSQL cannot clone a database that has active connections.
        _logger.info(f"Terminating connections to template DB '{template_db}'...")
        term_sql = (
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{template_db}' AND pid <> pg_backend_pid();"
        )
        subprocess.run(
            ['psql', '-X', '-tA', '-d', 'postgres', '-c', term_sql],
            capture_output=True, text=True, timeout=15
        )
        # Brief pause to let connections fully close
        import time
        time.sleep(1)

        # Create from template using SQL (more control than createdb utility).
        # Database names with dots/hyphens must be double-quoted in SQL.
        create_sql = f'CREATE DATABASE "{db_name}" TEMPLATE "{template_db}";'
        _logger.info(f"Creating database: {create_sql}")
        result = subprocess.run(
            ['psql', '-X', '-d', 'postgres', '-c', create_sql],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            raise Exception(f"Failed to create database: {result.stderr.strip()}")

        _logger.info(f"Successfully created database {db_name} from template {template_db}")
        return True

    def _copy_filestore(self, db_name):
        """Copy the template database's filestore to the new tenant database.

        Without this, the cloned DB references files (CSS, JS, images) that
        only exist in the template's filestore directory, causing 500 errors.
        """
        template_db = self._get_template_db_name()

        # Odoo stores filestores in ~/.local/share/Odoo/filestore/<dbname>/
        # The Odoo data dir can be read from config.
        import odoo
        data_dir = odoo.tools.config.get('data_dir', os.path.expanduser('~/.local/share/Odoo'))
        template_fs = os.path.join(data_dir, 'filestore', template_db)
        tenant_fs = os.path.join(data_dir, 'filestore', db_name)

        if not os.path.isdir(template_fs):
            _logger.warning(
                f"Template filestore not found at {template_fs} — "
                f"tenant will regenerate assets on first access."
            )
            return

        if os.path.exists(tenant_fs):
            _logger.info(f"Tenant filestore already exists at {tenant_fs}, skipping copy")
            return

        try:
            shutil.copytree(template_fs, tenant_fs)
            _logger.info(f"Copied filestore: {template_fs} → {tenant_fs}")
        except Exception as e:
            _logger.error(f"Filestore copy failed: {e}")
            # Non-fatal: assets will be regenerated on first access
            # but it will be slower

    def _regenerate_assets(self, db_name):
        """Clear and regenerate web assets in the tenant database.

        Even with a copied filestore, stale asset bundle records can cause
        mismatches. Clearing ir_attachment asset entries forces Odoo to
        rebuild them fresh on next request.
        """
        sql = """
            DELETE FROM ir_attachment
            WHERE res_model = 'ir.ui.view'
              AND name LIKE '%assets%';
        """
        if self._psql_execute(db_name, sql):
            _logger.info(f"Cleared stale asset bundles in {db_name}")
        else:
            _logger.warning(f"Could not clear asset bundles in {db_name} (non-fatal)")

    def _configure_tenant_db(self, db_name, subscription, tenant_id):
        """Configure database parameters using parameterized SQL (psql -v) to prevent injection."""
        base_domain = self._get_base_domain()
        tenant_url = f"https://{tenant_id}.{base_domain}"
        tenant_host = f"{tenant_id}.{base_domain}"
        company_name = subscription.partner_id.company_name or subscription.partner_id.name or 'SaaS Tenant'
        company_email = subscription.partner_id.email or ''

        # Step 1: Ensure required PostgreSQL extensions exist (pgcrypto for crypt() / gen_salt()).
        # Wrapped via psql -c to run as a single statement.
        ext_sql = "CREATE EXTENSION IF NOT EXISTS pgcrypto;"
        if not self._psql_execute(db_name, ext_sql):
            raise Exception("Failed to create pgcrypto extension on tenant DB")

        # Step 2: Run parameterized SQL via psql -v (psql substitutes :'var' as quoted-literal,
        # safely escaping single quotes — no f-string injection.).
        sql_script = """
            UPDATE ir_config_parameter SET value = :'web_url' WHERE key = 'web.base.url';
            UPDATE ir_config_parameter SET value = 'True' WHERE key = 'web.base.url.freeze';
            INSERT INTO ir_config_parameter (key, value) VALUES ('mail.catchall.domain', :'host')
                ON CONFLICT (key) DO UPDATE SET value = :'host';
            UPDATE res_company SET name = :'cname', email = :'cemail' WHERE id = 1;
            INSERT INTO ir_config_parameter (key, value) VALUES ('saas.tenant_id', :'tid')
                ON CONFLICT (key) DO UPDATE SET value = :'tid';
            INSERT INTO ir_config_parameter (key, value) VALUES ('saas.tenant_subscription_id', :'sub_id')
                ON CONFLICT (key) DO UPDATE SET value = :'sub_id';
            UPDATE ir_config_parameter SET value = 'False' WHERE key = 'base.load_demo_data';
        """
        params = {
            'web_url': tenant_url,
            'host': tenant_host,
            'cname': company_name,
            'cemail': company_email,
            'tid': tenant_id,
            'sub_id': str(subscription.id),
        }
        if not self._psql_execute(db_name, sql_script, params=params):
            raise Exception(f"Failed to configure tenant database {db_name}")

        _logger.info(f"Configured database {db_name}")

    def _store_allowed_modules(self, db_name, module_list):
        """Store the allowed module names in the tenant DB.

        The saas_tenant_guard module reads 'saas.allowed_modules' at
        runtime to decide which modules may be installed.  We also
        include saas_tenant_guard itself so it's in the allowed set.
        """
        all_allowed = set(module_list) | self.SYSTEM_TENANT_MODULES
        csv = ','.join(sorted(all_allowed))
        sql = """
            INSERT INTO ir_config_parameter (key, value, create_uid, write_uid, create_date, write_date)
            VALUES ('saas.allowed_modules', :'modules', 1, 1, now(), now())
            ON CONFLICT (key) DO UPDATE SET value = :'modules', write_date = now();
        """
        if self._psql_execute(db_name, sql, params={'modules': csv}):
            _logger.info(f"Stored {len(all_allowed)} allowed modules in {db_name}")
        else:
            _logger.warning(f"Failed to store allowed modules in {db_name}")

    def _store_ai_allowed_models(self, db_name, subscription):
        """Push this package's AIHR AI-model entitlement into the tenant DB as
        'perfecthr_ai.allowed_models' (CSV of model keys).

        perfecthr_ai_core reads this parameter to gate which AI models the tenant
        can see and run (see allowed_ai_models there). A package with an AI tier
        writes that tier's fixed model set; a package with NO AI tier writes the
        literal 'none' (all AI models hidden). We intentionally write an explicit
        value — never leave it absent — because an absent parameter means
        'unrestricted' (the non-breaking default for pre-existing deployments).
        """
        models = subscription.package_id.get_ai_allowed_models()
        value = ','.join(models) if models else 'none'
        sql = """
            INSERT INTO ir_config_parameter (key, value, create_uid, write_uid, create_date, write_date)
            VALUES ('perfecthr_ai.allowed_models', :'val', 1, 1, now(), now())
            ON CONFLICT (key) DO UPDATE SET value = :'val', write_date = now();
        """
        if self._psql_execute(db_name, sql, params={'val': value}):
            _logger.info("Stored AI-model entitlement (%s) in %s", value, db_name)
        else:
            _logger.warning("Failed to store AI-model entitlement in %s", db_name)

    # Ephemeral, tenant-owned params we never copy — the tenant's connector mints
    # its own short-lived runtime token from the (copied) license_key + tenant_id.
    _AIHR_EPHEMERAL_PARAMS = {
        'perfecthr_aihr.runtime_token',
        'perfecthr_aihr.runtime_token_expiry',
    }

    def _store_aihr_connector_config(self, db_name):
        """Seed the AIHR connector configuration into the tenant DB so the tenant
        reaches AIHR out-of-the-box — customers just log in and use it, with no
        manual per-tenant setup.

        The values are copied from THIS master's own ``perfecthr_aihr.*`` system
        parameters, so a dev master propagates the dev endpoints and the main
        master the main endpoints automatically. The short-lived runtime token is
        deliberately NOT copied: the tenant's connector auto-activates from the
        copied license_key + tenant_id to obtain (and refresh) its own token.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        params = ICP.search([('key', '=like', 'perfecthr_aihr.%')])
        pairs = [(p.key, p.value) for p in params
                 if p.key not in self._AIHR_EPHEMERAL_PARAMS and p.value]
        if not pairs:
            _logger.warning(
                "No perfecthr_aihr.* config on this master — tenant %s will have no "
                "AIHR connectivity until the master's AIHR connector is configured.",
                db_name)
            return
        sql = """
            INSERT INTO ir_config_parameter (key, value, create_uid, write_uid, create_date, write_date)
            VALUES (:'k', :'v', 1, 1, now(), now())
            ON CONFLICT (key) DO UPDATE SET value = :'v', write_date = now();
        """
        ok = 0
        for key, value in pairs:
            if self._psql_execute(db_name, sql, params={'k': key, 'v': value}):
                ok += 1
        _logger.info("Seeded %s/%s AIHR connector params into %s", ok, len(pairs), db_name)

    def _store_subscription_snapshot(self, db_name, subscription):
        """Push a read-only snapshot of the subscription into the tenant DB so the
        in-tenant dashboard (saas_tenant_dashboard) can display plan, status,
        renewal and quota without a live call back to the master.

        Stored as a JSON string in ir_config_parameter key 'saas.subscription_info'.
        Storage USAGE is computed live inside the tenant; this snapshot only
        carries the master-owned facts (plan, dates, limits, portal links).
        """
        pkg = subscription.package_id
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        sub_path = '/my/subscriptions/%s' % subscription.id
        info = {
            'package_name': pkg.name or '',
            'tier_level': pkg.tier_level,
            'billing_plan_label': subscription.billing_plan_label or '',
            'state': subscription.state or '',
            'is_trial': bool(subscription.is_trial),
            'date_start': subscription.date_start.isoformat() if subscription.date_start else '',
            'date_next_invoice': subscription.date_next_invoice.isoformat() if subscription.date_next_invoice else '',
            'date_end': subscription.date_end.isoformat() if subscription.date_end else '',
            'trial_end_date': subscription.trial_end_date.isoformat() if subscription.trial_end_date else '',
            'currency': pkg.currency_id.symbol or '৳',
            'monthly_price': pkg.get_duration_pricing(subscription.duration_months or 1).get(
                'monthly_price', pkg.monthly_price),
            'storage_limit_gb': pkg.storage_limit_gb or 0.0,
            'user_limit': pkg.user_limit or 0,
            'subscription_id': subscription.id,
            'subscription_ref': subscription.name or '',
            'portal_base_url': base_url,
            'manage_url': base_url + sub_path,
            'upgrade_url': base_url + sub_path + '/upgrade',
            'synced_at': fields.Datetime.now().isoformat(),
        }
        sql = """
            INSERT INTO ir_config_parameter (key, value, create_uid, write_uid, create_date, write_date)
            VALUES ('saas.subscription_info', :'info', 1, 1, now(), now())
            ON CONFLICT (key) DO UPDATE SET value = :'info', write_date = now();
        """
        if self._psql_execute(db_name, sql, params={'info': json.dumps(info)}):
            _logger.info("Stored subscription snapshot in %s", db_name)
            return True
        _logger.warning("Failed to store subscription snapshot in %s", db_name)
        return False

    def _install_modules(self, db_name, module_list):
        """Install selected modules in tenant database via Odoo CLI.

        IMPORTANT: We use `--init` (not `--update`) because the template DB
        only has base modules installed. `--update` only refreshes already-
        installed modules; `--init` actually installs new ones.

        `saas.odoo_bin_path` may include the Python interpreter,
        e.g. `/opt/odoo18/venv/bin/python3.12 /opt/odoo18/odoo-bin`. We split it
        with shlex so it runs correctly when shell=False.
        """
        if not module_list:
            _logger.info("No modules to install")
            return True

        module_string = ','.join(module_list)

        odoo_bin_str = self._get_odoo_bin_path()
        config_path = self._get_odoo_config_path()

        # Build argv list (no shell). Use --init to INSTALL new modules.
        argv = shlex.split(odoo_bin_str) + [
            '-c', config_path,
            '-d', db_name,
            '--init', module_string,
            '--stop-after-init',
            '--no-http',
            '--without-demo=all',
        ]
        _logger.info(f"Installing modules with argv: {argv}")

        result = subprocess.run(argv, capture_output=True, text=True, timeout=600)

        if result.returncode != 0:
            tail = (result.stderr or result.stdout or '')[-2000:]
            raise Exception(f"Module installation failed (exit {result.returncode}): {tail}")

        _logger.info(f"Successfully installed/updated modules: {module_string}")
        return True

    def _restrict_tenant_modules(self, db_name, allowed_modules):
        """Lock down the tenant's Apps page to ONLY show installed modules.

        This method runs AFTER `_install_modules(--init)` has installed all
        package modules and their dependencies.  At this point every module
        the tenant needs is already in state='installed'.

        Strategy (runs in order):
          1. Delete ir_model_data records that reference non-installed
             ir_module_module rows.  This prevents the "duplicate key"
             error when Odoo's update_list() would try to re-insert them.
          2. Delete ir_module_module_dependency rows that reference modules
             we are about to remove.
          3. Delete all ir_module_module rows whose state is NOT
             'installed' / 'to upgrade' / 'to install'.
             → The Apps page will only list installed modules.
          4. Disable the "Update Apps List", "Apply Scheduled Upgrades",
             and "Import Module" UI actions so the tenant admin cannot
             re-discover or side-load modules.
          5. Set application=false on system-level dependencies so only
             the actual package modules appear in the Apps grid.

        Everything is dynamic: we never hardcode module names.  We simply
        keep whatever `--init` installed and remove the rest.
        """
        if not allowed_modules:
            _logger.info("No module restriction needed (empty list)")
            return

        _logger.info(f"Restricting modules in {db_name} — keeping only installed modules")

        # ------------------------------------------------------------------
        # Step 1: Delete ir_model_data pointing to non-installed modules
        # ------------------------------------------------------------------
        sql_clean_imd = """
            DELETE FROM ir_model_data
             WHERE model = 'ir.module.module'
               AND res_id IN (
                   SELECT id FROM ir_module_module
                    WHERE state NOT IN ('installed', 'to upgrade', 'to install')
               );
        """
        self._psql_execute(db_name, sql_clean_imd)

        # ------------------------------------------------------------------
        # Step 2: Delete dependency records for non-installed modules
        # ------------------------------------------------------------------
        sql_clean_deps = """
            DELETE FROM ir_module_module_dependency
             WHERE module_id IN (
                   SELECT id FROM ir_module_module
                    WHERE state NOT IN ('installed', 'to upgrade', 'to install')
               );
        """
        self._psql_execute(db_name, sql_clean_deps)

        # Also clean up exclusion records if they exist
        sql_clean_excl = """
            DELETE FROM ir_module_module_exclusion
             WHERE module_id IN (
                   SELECT id FROM ir_module_module
                    WHERE state NOT IN ('installed', 'to upgrade', 'to install')
               );
        """
        self._psql_execute(db_name, sql_clean_excl)

        # ------------------------------------------------------------------
        # Step 3: Delete all non-installed module records
        # ------------------------------------------------------------------
        sql_delete_modules = """
            DELETE FROM ir_module_module
             WHERE state NOT IN ('installed', 'to upgrade', 'to install');
        """
        self._psql_execute(db_name, sql_delete_modules)

        # ------------------------------------------------------------------
        # Step 4: Hide "Update Apps List" and "Apply Scheduled Upgrades"
        #         menu items. The saas_tenant_guard module handles
        #         blocking at the Python level; this just hides the UI.
        #         NOTE: ir_act_window has no 'active' column in Odoo 18,
        #         so we only disable the menu entries.
        # ------------------------------------------------------------------
        sql_disable_menus = """
            UPDATE ir_ui_menu
               SET active = false
             WHERE id IN (
                SELECT res_id FROM ir_model_data
                 WHERE model = 'ir.ui.menu'
                   AND name IN (
                       'menu_module_updates',
                       'menu_module_upgrades'
                   )
             );
        """
        self._psql_execute(db_name, sql_disable_menus)

        # ------------------------------------------------------------------
        # Step 5: Set application=true only for the actual package modules;
        #         hide system dependencies from the Apps grid.
        # ------------------------------------------------------------------
        # Validate and quote the allowed module names
        quoted = ",".join(
            f"'{m}'" for m in allowed_modules
            if re.match(r'^[a-zA-Z0-9_]+$', m)
        )
        if quoted:
            sql_show = f"""
                UPDATE ir_module_module SET application = true
                 WHERE name IN ({quoted});
            """
            sql_hide = f"""
                UPDATE ir_module_module SET application = false
                 WHERE name NOT IN ({quoted});
            """
            self._psql_execute(db_name, sql_show)
            self._psql_execute(db_name, sql_hide)

        # Count what remains
        count_result = subprocess.run(
            ['psql', '-X', '-tA', '-d', db_name, '-c',
             "SELECT count(*) FROM ir_module_module;"],
            capture_output=True, text=True, timeout=15
        )
        remaining = count_result.stdout.strip() if count_result.returncode == 0 else '?'

        _logger.info(
            f"Module restriction complete in {db_name}: "
            f"{remaining} modules remain (all installed), "
            f"Update Apps List disabled"
        )

    def _create_admin_user(self, db_name, subscription):
        """Reset the cloned tenant's admin (id=2) password and update its identity.

        The template DB already contains the standard Odoo `admin` user (res_users.id = 2)
        with a linked res_partner. We rotate that user's password to a fresh secret,
        rename it to the customer's name+email, and ensure its company is the default.

        Odoo 18 stores plaintext in the `password` column and hashes it
        internally when the user first logs in (via _crypt_context).
        Do NOT use pgcrypto crypt() — Odoo won't recognise the hash.
        """
        admin_password = self._generate_secure_password()
        admin_login = subscription.partner_id.email or subscription.partner_id.name or 'admin@saas.tenant'
        admin_name = subscription.partner_id.name or 'Tenant Administrator'

        sql_script = """
            -- Update admin login and set PLAINTEXT password
            -- (Odoo 18 hashes it on first use via _crypt_context).
            UPDATE res_users
                SET login = :'login',
                    password = :'pwd'
                WHERE id = 2;
            -- Update linked partner identity for cleaner UI.
            UPDATE res_partner
                SET name = :'pname',
                    email = :'login'
                WHERE id = (SELECT partner_id FROM res_users WHERE id = 2);
        """
        params = {
            'login': admin_login,
            'pwd': admin_password,
            'pname': admin_name,
        }
        if not self._psql_execute(db_name, sql_script, params=params):
            raise Exception("Failed to set tenant admin password")

        _logger.info(f"Reset tenant admin (login={admin_login}) password for database {db_name}")
        return admin_password

    def _update_nginx_config(self, tenant_id, tenant_domain, db_name):
        """Write Nginx vhost for tenant — Phase 1: HTTP-only with ACME challenge.

        Creates an HTTP (port 80) server block that:
          1. Serves Let's Encrypt ACME challenge files from a local directory
          2. Proxies everything else to Odoo

        After this, _setup_tenant_ssl() will:
          1. Run certbot --webroot to obtain a certificate
          2. Rewrite this config to include the SSL block
        """
        nginx_config_dir = self._get_nginx_config_dir()
        sites_available = f"{nginx_config_dir}/sites-available/{tenant_domain}.conf"
        sites_enabled = f"{nginx_config_dir}/sites-enabled/{tenant_domain}.conf"

        # ACME webroot — configurable via system parameter
        webroot = self.env['ir.config_parameter'].sudo().get_param(
            'saas.acme_webroot', '/var/www/letsencrypt'
        )

        # Ensure the webroot directory tree exists.
        # Uses 'bash -c' via sudo to create the full path in one call,
        # avoiding the need for separate sudoers entries for mkdir/chmod.
        subprocess.run(
            ['sudo', '-n', 'bash', '-c',
             f'mkdir -p {webroot}/.well-known/acme-challenge && chmod -R 755 {webroot}'],
            capture_output=True, timeout=10
        )
        # Verify it was actually created
        check = subprocess.run(
            ['sudo', '-n', 'test', '-d', f'{webroot}/.well-known/acme-challenge'],
            capture_output=True, timeout=5
        )
        if check.returncode != 0:
            _logger.error(
                f"ACME webroot {webroot} does not exist and could not be created. "
                f"Run manually: sudo mkdir -p {webroot}/.well-known/acme-challenge && "
                f"sudo chmod -R 755 {webroot}"
            )

        nginx_config = self._build_http_config(tenant_domain, webroot)

        try:
            tee_proc = subprocess.run(
                ['sudo', '-n', 'tee', sites_available],
                input=nginx_config, capture_output=True, text=True, timeout=30
            )
            if tee_proc.returncode != 0:
                raise Exception(f"sudo tee failed: {tee_proc.stderr.strip()}")

            ln_proc = subprocess.run(
                ['sudo', '-n', 'ln', '-sfn', sites_available, sites_enabled],
                capture_output=True, text=True, timeout=15
            )
            if ln_proc.returncode != 0:
                raise Exception(f"sudo ln failed: {ln_proc.stderr.strip()}")

            self._reload_nginx()
            _logger.info(f"Phase 1: Nginx HTTP config ready for {tenant_domain}")
        except Exception as e:
            _logger.error(f"Failed to update Nginx config: {e}")
            raise Exception(f"Nginx configuration failed: {e}")

    def _setup_tenant_ssl(self, tenant_domain):
        """Obtain SSL cert via certbot webroot and rewrite nginx with HTTPS.

        This is a bulletproof 2-step approach:
          Step 1: certbot certonly --webroot
            - Uses the /.well-known/acme-challenge/ location from the
              HTTP config that _update_nginx_config() already set up
            - Certbot places a token file, Let's Encrypt fetches it via
              HTTP on port 80 — fully automatic, no DNS TXT needed
            - Certificate files end up in /etc/letsencrypt/live/<domain>/

          Step 2: Rewrite nginx config
            - We write a new config with both HTTP→HTTPS redirect
              and the HTTPS server block pointing to the cert files
            - We reload nginx ourselves (no certbot --nginx plugin)

        Requires sudoers NOPASSWD for: certbot, tee, nginx, systemctl
        """
        admin_email = self.env['ir.config_parameter'].sudo().get_param(
            'saas.admin_email', 'admin@perfecthr.net'
        )
        webroot = '/var/www/letsencrypt'
        nginx_config_dir = self._get_nginx_config_dir()
        sites_available = f"{nginx_config_dir}/sites-available/{tenant_domain}.conf"

        import time
        time.sleep(2)  # Ensure nginx reload is complete

        # ── Step 1: Obtain certificate via webroot ──
        try:
            _logger.info(f"Requesting SSL cert for {tenant_domain} via webroot HTTP-01")

            result = subprocess.run(
                [
                    'sudo', '-n', 'certbot', 'certonly',
                    '--webroot',
                    '-w', webroot,
                    '-d', tenant_domain,
                    '--non-interactive',
                    '--agree-tos',
                    '-m', admin_email,
                ],
                capture_output=True, text=True, timeout=180
            )

            if result.returncode != 0:
                stderr = result.stderr.strip()[:500]
                stdout = result.stdout.strip()[:500]
                _logger.warning(
                    f"Certbot webroot failed for {tenant_domain}: "
                    f"stderr={stderr} stdout={stdout}"
                )
                # Leave HTTP-only config — still functional
                return False

            _logger.info(f"SSL certificate obtained for {tenant_domain}")

        except subprocess.TimeoutExpired:
            _logger.warning(f"Certbot timed out for {tenant_domain}")
            return False
        except Exception as e:
            _logger.warning(f"SSL cert request failed for {tenant_domain}: {e}")
            return False

        # ── Step 2: Rewrite nginx config with HTTPS ──
        cert_path = f"/etc/letsencrypt/live/{tenant_domain}/fullchain.pem"
        key_path = f"/etc/letsencrypt/live/{tenant_domain}/privkey.pem"

        # Verify cert files exist before rewriting
        check = subprocess.run(
            ['sudo', '-n', 'test', '-f', cert_path],
            capture_output=True, timeout=10
        )
        if check.returncode != 0:
            _logger.warning(
                f"Cert file not found at {cert_path} after certbot success — "
                f"keeping HTTP-only config"
            )
            return False

        https_config = self._build_https_config(tenant_domain, webroot, cert_path, key_path)

        try:
            tee_proc = subprocess.run(
                ['sudo', '-n', 'tee', sites_available],
                input=https_config, capture_output=True, text=True, timeout=30
            )
            if tee_proc.returncode != 0:
                _logger.warning(f"Failed to write HTTPS nginx config: {tee_proc.stderr}")
                return False

            self._reload_nginx()
            _logger.info(f"Phase 2: Nginx HTTPS config active for {tenant_domain}")
            return True

        except Exception as e:
            _logger.warning(f"Failed to activate HTTPS config for {tenant_domain}: {e}")
            return False

    # ─── Nginx Config Builders ───

    def _build_http_config(self, tenant_domain, webroot):
        """Build HTTP-only nginx config with ACME challenge location."""
        return f"""# SaaS Tenant: {tenant_domain}
# Phase 1: HTTP-only (SSL will be added automatically)
server {{
    listen 80;
    server_name {tenant_domain};

    # Let's Encrypt ACME challenge (must be BEFORE the proxy)
    location /.well-known/acme-challenge/ {{
        root {webroot};
        allow all;
    }}

    proxy_read_timeout 720s;
    proxy_connect_timeout 720s;
    proxy_send_timeout 720s;

    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Real-IP $remote_addr;

    client_max_body_size 200M;

    access_log /var/log/nginx/{tenant_domain}_access.log;
    error_log /var/log/nginx/{tenant_domain}_error.log;

    location / {{
        proxy_redirect off;
        proxy_pass http://127.0.0.1:8069;
    }}

    location /longpolling/ {{
        proxy_pass http://127.0.0.1:8072;
    }}

    location /websocket {{
        proxy_pass http://127.0.0.1:8072;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}

    location ~* /web/static/ {{
        proxy_cache_valid 200 60m;
        proxy_buffering on;
        expires 864000;
        proxy_pass http://127.0.0.1:8069;
    }}
}}
"""

    def _build_https_config(self, tenant_domain, webroot, cert_path, key_path):
        """Build full HTTPS nginx config with HTTP→HTTPS redirect."""
        return f"""# SaaS Tenant: {tenant_domain}
# Phase 2: HTTPS active (auto-generated)
server {{
    listen 80;
    server_name {tenant_domain};

    # Let's Encrypt renewal
    location /.well-known/acme-challenge/ {{
        root {webroot};
        allow all;
    }}

    # Redirect all other HTTP traffic to HTTPS
    location / {{
        return 301 https://$host$request_uri;
    }}
}}

server {{
    listen 443 ssl http2;
    server_name {tenant_domain};

    ssl_certificate {cert_path};
    ssl_certificate_key {key_path};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    proxy_read_timeout 720s;
    proxy_connect_timeout 720s;
    proxy_send_timeout 720s;

    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Real-IP $remote_addr;

    client_max_body_size 200M;

    access_log /var/log/nginx/{tenant_domain}_access.log;
    error_log /var/log/nginx/{tenant_domain}_error.log;

    location / {{
        proxy_redirect off;
        proxy_pass http://127.0.0.1:8069;
    }}

    location /longpolling/ {{
        proxy_pass http://127.0.0.1:8072;
    }}

    location /websocket {{
        proxy_pass http://127.0.0.1:8072;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }}

    location ~* /web/static/ {{
        proxy_cache_valid 200 60m;
        proxy_buffering on;
        expires 864000;
        proxy_pass http://127.0.0.1:8069;
    }}
}}
"""

    def _reload_nginx(self):
        """Test then reload Nginx via sudo (NOPASSWD sudoers required)."""
        test = subprocess.run(
            ['sudo', '-n', 'nginx', '-t'],
            capture_output=True, text=True, timeout=15
        )
        if test.returncode != 0:
            raise Exception(f"nginx -t failed: {test.stderr.strip()}")

        reload_proc = subprocess.run(
            ['sudo', '-n', 'systemctl', 'reload', 'nginx'],
            capture_output=True, text=True, timeout=15
        )
        if reload_proc.returncode != 0:
            raise Exception(f"systemctl reload nginx failed: {reload_proc.stderr.strip()}")
        _logger.info("Nginx reloaded successfully")

    def _rollback_tenant_db(self, db_name, tenant_domain=None):
        """Rollback: Drop database and remove Nginx vhost if provisioning failed midway."""
        if not re.match(r'^[a-z0-9_.-]+$', db_name or ''):
            return
        try:
            # Terminate connections first
            term_sql = (
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid();"
            )
            subprocess.run(
                ['psql', '-X', '-tA', '-d', 'postgres', '-c', term_sql],
                capture_output=True, text=True, timeout=10
            )
            import time
            time.sleep(1)
            # Drop with quoted identifier (handles dots in name)
            drop_sql = f'DROP DATABASE IF EXISTS "{db_name}";'
            subprocess.run(
                ['psql', '-X', '-d', 'postgres', '-c', drop_sql],
                capture_output=True, timeout=30
            )
            _logger.info(f"Rolled back: dropped database {db_name}")
        except Exception as e:
            _logger.error(f"Failed to drop database during rollback: {e}")

        if tenant_domain:
            try:
                ndir = self._get_nginx_config_dir()
                subprocess.run(['sudo', '-n', 'rm', '-f',
                                f"{ndir}/sites-enabled/{tenant_domain}.conf",
                                f"{ndir}/sites-available/{tenant_domain}.conf"],
                               capture_output=True, timeout=15)
                subprocess.run(['sudo', '-n', 'systemctl', 'reload', 'nginx'],
                               capture_output=True, timeout=15)
            except Exception as e:
                _logger.warning(f"Nginx rollback cleanup failed (non-fatal): {e}")

    # ==================== HELPER METHODS ====================

    def _psql_execute(self, db_name, sql, params=None, fetch=False):
        """Execute SQL on a tenant database via the local psql client.

        Uses argv (shell=False) and passes user-controlled values through
        psql's `-v key=value` mechanism with `:'key'` placeholders, which
        psql safely quotes as SQL string literals. Avoids any f-string injection.

        Returns:
            - if fetch=True: stdout string
            - else: True on success / False on failure
        """
        argv = ['psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-d', db_name]
        for k, v in (params or {}).items():
            # `psql -v name=val` — combined with `:'name'` in the script — is the
            # documented way to inject quoted-literal values safely.
            argv += ['-v', f"{k}={v}"]
        # Pipe SQL on stdin instead of -c, so multi-statement scripts work cleanly.
        try:
            result = subprocess.run(
                argv, input=sql, capture_output=True, text=True, timeout=60
            )
        except Exception as e:
            _logger.error(f"psql exec error on {db_name}: {e}")
            return '' if fetch else False

        if result.returncode != 0:
            _logger.error(
                f"psql failed on {db_name} (exit {result.returncode}): {result.stderr.strip()}"
            )
            return '' if fetch else False
        return result.stdout if fetch else True

    # Back-compat shim for any older callers — unused going forward.
    def _execute_sql(self, db_name, sql, fetch=False):
        return self._psql_execute(db_name, sql, fetch=fetch)

    def _get_available_modules(self, db_name):
        """Get list of installed modules in tenant database (one per line)."""
        sql = "SELECT name FROM ir_module_module WHERE state = 'installed';"
        # `-A -t` = unaligned, tuples-only output (no header, no row count).
        argv = ['psql', '-X', '-A', '-t', '-d', db_name, '-c', sql]
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        except Exception as e:
            _logger.error(f"List modules failed: {e}")
            return []
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.split('\n') if line.strip()]

    def _get_template_db_name(self):
        """Get template database name from system parameters"""
        template_db = self.env['ir.config_parameter'].sudo().get_param(
            'saas.template_db_name', 
            'template_odoo'
        )
        return template_db

    def _get_base_domain(self):
        """Get base domain from system parameters"""
        base_domain = self.env['ir.config_parameter'].sudo().get_param(
            'saas.domain_base',
            'saas.yourdomain.com'
        )
        return base_domain

    def _get_odoo_bin_path(self):
        """Get Odoo binary path"""
        odoo_bin = self.env['ir.config_parameter'].sudo().get_param(
            'saas.odoo_bin_path',
            '/usr/bin/odoo'
        )
        return odoo_bin

    def _get_odoo_config_path(self):
        """Get Odoo configuration file path"""
        config_path = self.env['ir.config_parameter'].sudo().get_param(
            'saas.odoo_config_path',
            '/etc/odoo/odoo.conf'
        )
        return config_path

    def _get_nginx_config_dir(self):
        """Get Nginx configuration directory"""
        nginx_dir = self.env['ir.config_parameter'].sudo().get_param(
            'saas.nginx_config_dir',
            '/etc/nginx'
        )
        return nginx_dir


    def _encrypt_password(self, password):
        """Encrypt password using Fernet"""
        key_param = self.env['ir.config_parameter'].sudo().get_param('saas.encryption_key')
        if not key_param:
            key = Fernet.generate_key()
            self.env['ir.config_parameter'].sudo().set_param('saas.encryption_key', key.decode())
            key_param = key.decode()
        
        fernet = Fernet(key_param.encode())
        encrypted = fernet.encrypt(password.encode())
        return base64.b64encode(encrypted)

    def _send_credentials_email(self, subscription, tenant_domain, admin_password):
        """Send welcome email with tenant credentials"""
        try:
            template = self.env.ref('saas_subscription.email_template_tenant_credentials', False)
            if template:
                template.with_context(
                    tenant_domain=tenant_domain,
                    admin_password=admin_password
                ).send_mail(subscription.id, force_send=True)
        except Exception as e:
            _logger.warning(f"Failed to send credentials email: {e}")


class SaasSubscription(models.Model):
    _inherit = 'saas.subscription'

    def _trigger_provisioning(self):
        """Override: Trigger tenant provisioning"""
        self.ensure_one()
        _logger.info(f"Triggering provisioning for subscription {self.name}")
        
        # Check if already provisioning
        existing = self.env['tenant.provisioner'].search([
            ('subscription_id', '=', self.id),
            ('state', 'in', ['pending', 'provisioning'])
        ])
        
        if existing:
            _logger.info(f"Provisioning already in progress for {self.name}")
            return
        
        # Start provisioning asynchronously
        provisioner = self.env['tenant.provisioner']
        
        # Use cron or background thread (simplified: call directly)
        # For production, use Odoo's @api.model_cron or queue job
        provisioner.sudo().provision_tenant(self)
