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
            
            # Step 4: Configure Odoo database
            provisioner._configure_tenant_db(db_name, subscription, tenant_id)
            
            # Step 5: Install selected modules
            module_list = subscription.package_id.module_ids.mapped('name')
            provisioner._install_modules(db_name, module_list)
            
            # Step 6: Reset/configure tenant admin in tenant DB
            admin_password = provisioner._create_admin_user(db_name, subscription)
            
            # Step 7: Configure Nginx routing (tenant_domain == db_name with dbfilter)
            provisioner._update_nginx_config(tenant_id, tenant_domain, db_name)
            
            # Step 8: Update subscription record
            encrypted_password = provisioner._encrypt_password(db_password)
            subscription.write({
                'tenant_db_name': db_name,
                'tenant_db_password': encrypted_password,
                'tenant_url': f"http://{tenant_domain}",
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
        """Write Nginx vhost for tenant via `sudo tee` (works under non-root Odoo user).

        The Odoo service runs as user `odoo18`, who cannot write to /etc/nginx/* directly.
        Sudoers MUST grant NOPASSWD to user `odoo18` for these specific commands:
            /usr/bin/tee /etc/nginx/sites-available/*
            /bin/ln -sfn /etc/nginx/sites-available/* /etc/nginx/sites-enabled/*
            /usr/sbin/nginx -t
            /bin/systemctl reload nginx
        See deployment docs for the exact sudoers snippet.
        """
        nginx_config_dir = self._get_nginx_config_dir()
        sites_available = f"{nginx_config_dir}/sites-available/{tenant_domain}.conf"
        sites_enabled = f"{nginx_config_dir}/sites-enabled/{tenant_domain}.conf"
        base_domain = self._get_base_domain()

        nginx_config = f"""# SaaS Tenant Configuration for {tenant_domain}
server {{
    listen 80;
    server_name {tenant_domain};

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
        try:
            # Write config via `sudo tee`. We pipe stdin to avoid putting the
            # config in a shell argv (which can blow past arg-length limits).
            tee_proc = subprocess.run(
                ['sudo', '-n', 'tee', sites_available],
                input=nginx_config, capture_output=True, text=True, timeout=30
            )
            if tee_proc.returncode != 0:
                raise Exception(f"sudo tee failed: {tee_proc.stderr.strip()}")

            # Create/replace symlink in sites-enabled.
            ln_proc = subprocess.run(
                ['sudo', '-n', 'ln', '-sfn', sites_available, sites_enabled],
                capture_output=True, text=True, timeout=15
            )
            if ln_proc.returncode != 0:
                raise Exception(f"sudo ln failed: {ln_proc.stderr.strip()}")

            self._reload_nginx()
            _logger.info(f"Updated Nginx configuration for {tenant_domain}")
        except Exception as e:
            _logger.error(f"Failed to update Nginx config: {e}")
            raise Exception(f"Nginx configuration failed: {e}")

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
