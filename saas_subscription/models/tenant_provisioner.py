from odoo import models, fields, api, _
from odoo.exceptions import UserError
import subprocess
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
            
            # Step 5: Install selected modules
            module_list = subscription.package_id.module_ids.mapped('name')
            provisioner._install_modules(db_name, module_list)
            
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

    # ==================== PROVISIONING STEPS ====================
    
    def _generate_tenant_id(self, subscription):
        """Generate unique tenant identifier"""
        import hashlib
        unique_string = f"{subscription.id}_{subscription.name}_{datetime.now().timestamp()}"
        hash_object = hashlib.md5(unique_string.encode())
        tenant_id = hash_object.hexdigest()[:12]
        return tenant_id

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

    def _install_modules(self, db_name, module_list):
        """Install/update selected modules in tenant database via Odoo CLI.

        IMPORTANT: `saas.odoo_bin_path` may include the Python interpreter,
        e.g. `/opt/odoo18/venv/bin/python3.12 /opt/odoo18/odoo-bin`. We split it
        with shlex so it runs correctly when shell=False.
        """
        if not module_list:
            _logger.info("No modules to install")
            return True

        # The cloned template already has these installed; re-running --update is idempotent.
        module_string = ','.join(module_list)

        odoo_bin_str = self._get_odoo_bin_path()
        config_path = self._get_odoo_config_path()

        # Build argv list (no shell). odoo_bin_str may be "<python> <odoo-bin>" or just one path.
        argv = shlex.split(odoo_bin_str) + [
            '-c', config_path,
            '-d', db_name,
            '--update', module_string,
            '--stop-after-init',
            '--no-http',
        ]
        _logger.info(f"Installing modules with argv: {argv}")

        result = subprocess.run(argv, capture_output=True, text=True, timeout=600)

        if result.returncode != 0:
            tail = (result.stderr or result.stdout or '')[-2000:]
            raise Exception(f"Module installation failed (exit {result.returncode}): {tail}")

        _logger.info(f"Successfully installed/updated modules: {module_string}")
        return True

    def _restrict_tenant_modules(self, db_name, allowed_modules):
        """Completely restrict the tenant's Apps page to package modules only.

        Strategy:
          1. Build an allow-list: package modules + their installed
             dependencies + essential system/base modules.
          2. DELETE all ir_module_module rows that are NOT installed
             and NOT in the allow-list. This removes them entirely
             from the Apps menu — they simply don't exist.
          3. For installed modules that aren't in the allow-list
             (i.e. system dependencies), clear their `application`
             flag so they don't show on the Apps grid.

        Result: tenants see ONLY their package apps, no matter what
        filter they apply.
        """
        if not allowed_modules:
            _logger.info("No module restriction needed (empty list)")
            return

        # System/infrastructure modules that must remain in the DB
        # even if they aren't in the package (they're dependencies).
        SYSTEM_MODULES = [
            'base', 'web', 'bus', 'base_setup', 'iap',
            'mail', 'auth_signup', 'web_editor', 'http_routing',
            'web_tour', 'digest', 'portal', 'website',
            'base_import', 'web_kanban', 'web_cohort',
            'web_dashboard', 'spreadsheet', 'spreadsheet_dashboard',
        ]

        # Build the full allow-list: package modules + system essentials
        all_allowed = set(allowed_modules) | set(SYSTEM_MODULES)
        # Validate module names for SQL safety
        quoted = ",".join(f"'{m}'" for m in all_allowed if re.match(r'^[a-zA-Z0-9_]+$', m))

        if not quoted:
            _logger.warning("No valid module names to allow — skipping restriction")
            return

        # Step 1: DELETE non-installed modules that aren't in the allow-list.
        # This completely removes them from ir_module_module — the tenant
        # will never see them in the Apps menu, regardless of filters.
        sql_delete = f"""
            DELETE FROM ir_module_module
             WHERE name NOT IN ({quoted})
               AND state NOT IN ('installed', 'to upgrade', 'to install');
        """
        if self._psql_execute(db_name, sql_delete):
            _logger.info(
                f"Deleted non-package module records from {db_name}"
            )
        else:
            _logger.warning(f"Failed to delete modules in {db_name} (non-fatal)")

        # Step 2: For installed modules NOT in the allow-list (system deps),
        # clear `application = true` so they don't show on the Apps grid.
        sql_hide = f"""
            UPDATE ir_module_module
               SET application = false
             WHERE name NOT IN ({quoted})
               AND application = true;
        """
        self._psql_execute(db_name, sql_hide)

        _logger.info(
            f"Module restriction complete in {db_name}: "
            f"{len(all_allowed)} modules allowed, rest deleted/hidden"
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
