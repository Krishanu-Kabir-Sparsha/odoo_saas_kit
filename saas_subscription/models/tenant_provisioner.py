from odoo import models, fields, api, _
from odoo.exceptions import UserError
import subprocess
import logging
import secrets
import string
import base64
import os
import re
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
            
            # Step 1: Generate tenant identifiers
            tenant_id = provisioner._generate_tenant_id(subscription)
            db_name = f"tenant_{tenant_id}"
            
            # Step 2: Generate secure password
            db_password = provisioner._generate_secure_password()
            
            # Step 3: Create PostgreSQL database from template
            provisioner._create_tenant_db(db_name)
            
            # Step 4: Configure Odoo database
            provisioner._configure_tenant_db(db_name, subscription, tenant_id)
            
            # Step 5: Install selected modules
            module_list = subscription.package_id.module_ids.mapped('name')
            provisioner._install_modules(db_name, module_list)
            
            # Step 6: Create admin user in tenant DB
            admin_password = provisioner._create_admin_user(db_name)
            
            # Step 7: Configure Nginx routing
            tenant_domain = f"{tenant_id}.{provisioner._get_base_domain()}"
            provisioner._update_nginx_config(tenant_id, tenant_domain, db_name)
            
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
            
            # Rollback: Drop database if created
            if 'db_name' in locals():
                provisioner._rollback_tenant_db(db_name)
            
            subscription.write({
                'state': 'provisioning_failed',
                'state_reason': f"Provisioning failed: {error_msg[:200]}"
            })
            subscription._log_state_change(subscription.state, 'provisioning_failed', error_msg)
            
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
        """Create PostgreSQL database from template"""
        template_db = self._get_template_db_name()
        
        # Check if database already exists
        check_cmd = f"psql -lqt | cut -d \\| -f 1 | grep -qw {db_name}"
        result = subprocess.run(check_cmd, shell=True, capture_output=True)
        
        if result.returncode == 0:
            raise Exception(f"Database {db_name} already exists")
        
        # Create database from template
        cmd = f"createdb -T {template_db} {db_name}"
        _logger.info(f"Creating database: {cmd}")
        
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise Exception(f"Failed to create database: {result.stderr}")
        
        _logger.info(f"Successfully created database {db_name}")
        return True

    def _configure_tenant_db(self, db_name, subscription, tenant_id):
        """Configure database parameters"""
        base_domain = self._get_base_domain()
        
        # SQL commands to configure the tenant database
        sql_commands = [
            # Set base URL
            f"UPDATE ir_config_parameter SET value = 'https://{tenant_id}.{base_domain}' WHERE key = 'web.base.url';",
            f"UPDATE ir_config_parameter SET value = '{tenant_id}.{base_domain}' WHERE key = 'web.base.url.freeze';",
            # Set mail domain
            f"UPDATE ir_config_parameter SET value = '{tenant_id}.{base_domain}' WHERE key = 'mail.catchall.domain';",
            # Set company name
            f"UPDATE res_company SET name = '{subscription.partner_id.company_name or subscription.partner_id.name}' WHERE id = 1;",
            # Set company email
            f"UPDATE res_company SET email = '{subscription.partner_id.email}' WHERE id = 1;",
            # Store tenant ID for reference
            f"INSERT INTO ir_config_parameter (key, value) VALUES ('saas.tenant_id', '{subscription.id}') ON CONFLICT (key) DO UPDATE SET value = '{subscription.id}';",
            # Disable demo data
            f"UPDATE ir_config_parameter SET value = 'False' WHERE key = 'base.load_demo_data';"
        ]
        
        for sql in sql_commands:
            result = self._execute_sql(db_name, sql)
            if not result:
                _logger.warning(f"SQL command may have failed: {sql}")
        
        _logger.info(f"Configured database {db_name}")

    def _install_modules(self, db_name, module_list):
        """Install selected modules in tenant database"""
        if not module_list:
            _logger.info("No modules to install")
            return True
        
        # Filter only available modules
        available_modules = self._get_available_modules(db_name)
        modules_to_install = [m for m in module_list if m in available_modules]
        
        if not modules_to_install:
            _logger.warning(f"No installable modules found from list: {module_list}")
            return True
        
        # Build module list string
        module_string = ','.join(modules_to_install)
        
        # Odoo command to install modules
        odoo_bin = self._get_odoo_bin_path()
        config_path = self._get_odoo_config_path()
        
        # Use Odoo CLI to update/init modules
        cmd = f"{odoo_bin} -c {config_path} -d {db_name} --update {module_string} --stop-after-init --no-http"
        
        _logger.info(f"Installing modules with command: {cmd}")
        
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
        
        if result.returncode != 0:
            raise Exception(f"Module installation failed: {result.stderr}")
        
        _logger.info(f"Successfully installed modules: {module_string}")
        return True

    def _create_admin_user(self, db_name):
        """Create admin user in tenant database"""
        admin_password = self._generate_secure_password()
        
        # Hash password using Odoo's method
        # We'll use a simpler approach: create via SQL and set password
        # In production, use Odoo's res.users model via XML-RPC
        
        # Check if admin user exists
        check_sql = "SELECT id FROM res_users WHERE login = 'admin@saas.tenant'"
        existing = self._execute_sql(db_name, check_sql, fetch=True)
        
        if existing:
            # Update existing admin
            sql = f"""
                UPDATE res_users 
                SET password = crypt('{admin_password}', gen_salt('bf'))
                WHERE login = 'admin@saas.tenant'
            """
        else:
            # Create new admin user
            sql = f"""
                INSERT INTO res_users (
                    login, password, name, active, company_id, create_date
                ) VALUES (
                    'admin@saas.tenant',
                    crypt('{admin_password}', gen_salt('bf')),
                    'Tenant Administrator',
                    True,
                    1,
                    NOW()
                )
            """
        
        self._execute_sql(db_name, sql)
        _logger.info(f"Created admin user for database {db_name}")
        
        return admin_password

    def _update_nginx_config(self, tenant_id, tenant_domain, db_name):
        """Update Nginx configuration for tenant routing"""
        nginx_config_dir = self._get_nginx_config_dir()
        nginx_config_file = f"{nginx_config_dir}/sites-available/{tenant_domain}.conf"
        
        # Nginx configuration template
        nginx_config = f"""# SaaS Tenant Configuration for {tenant_domain}
server {{
    listen 80;
    server_name {tenant_domain};
    return 301 https://$server_name$request_uri;
}}

server {{
    listen 443 ssl http2;
    server_name {tenant_domain};

    # SSL certificates (adjust paths as needed)
    ssl_certificate /etc/nginx/ssl/{self._get_base_domain()}.crt;
    ssl_certificate_key /etc/nginx/ssl/{self._get_base_domain()}.key;

    # Logs
    access_log /var/log/nginx/{tenant_domain}_access.log;
    error_log /var/log/nginx/{tenant_domain}_error.log;

    # Proxy to Odoo
    location / {{
        proxy_pass http://127.0.0.1:8069;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # Timeouts
        proxy_connect_timeout 3600;
        proxy_send_timeout 3600;
        proxy_read_timeout 3600;
        
        # Longpolling
        proxy_buffering off;
    }}

    # Static files cache
    location ~* /web/static/ {{
        proxy_cache_valid 200 60m;
        proxy_buffering on;
        expires 864000;
        proxy_pass http://127.0.0.1:8069;
    }}
}}
"""
        
        # Write configuration file
        try:
            with open(nginx_config_file, 'w') as f:
                f.write(nginx_config)
            
            # Create symlink to sites-enabled
            enabled_file = f"{nginx_config_dir}/sites-enabled/{tenant_domain}.conf"
            if not os.path.exists(enabled_file):
                os.symlink(nginx_config_file, enabled_file)
            
            # Test and reload Nginx
            self._reload_nginx()
            
            _logger.info(f"Updated Nginx configuration for {tenant_domain}")
        except Exception as e:
            _logger.error(f"Failed to update Nginx config: {e}")
            raise Exception(f"Nginx configuration failed: {e}")

    def _reload_nginx(self):
        """Reload Nginx configuration"""
        # Test configuration first
        test_cmd = "nginx -t"
        result = subprocess.run(test_cmd, shell=True, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise Exception(f"Nginx configuration test failed: {result.stderr}")
        
        # Reload
        reload_cmd = "systemctl reload nginx"
        result = subprocess.run(reload_cmd, shell=True, capture_output=True, text=True)
        
        if result.returncode != 0:
            # Try alternative reload method
            alt_cmd = "nginx -s reload"
            result = subprocess.run(alt_cmd, shell=True, capture_output=True, text=True)
            if result.returncode != 0:
                raise Exception(f"Nginx reload failed: {result.stderr}")
        
        _logger.info("Nginx reloaded successfully")

    def _rollback_tenant_db(self, db_name):
        """Rollback: Drop database if provisioning fails"""
        try:
            cmd = f"dropdb --if-exists {db_name}"
            subprocess.run(cmd, shell=True, capture_output=True, timeout=30)
            _logger.info(f"Rolled back: dropped database {db_name}")
        except Exception as e:
            _logger.error(f"Failed to drop database during rollback: {e}")

    # ==================== HELPER METHODS ====================
    
    def _execute_sql(self, db_name, sql, fetch=False):
        """Execute SQL command on tenant database"""
        try:
            cmd = f'psql -d {db_name} -c "{sql}"'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            
            if fetch:
                return result.stdout
            return result.returncode == 0
        except Exception as e:
            _logger.error(f"SQL execution failed: {e}")
            return False

    def _get_available_modules(self, db_name):
        """Get list of installed/available modules in tenant database"""
        sql = "SELECT name FROM ir_module_module WHERE state = 'installed'"
        output = self._execute_sql(db_name, sql, fetch=True)
        
        if output:
            # Parse psql output to extract module names
            modules = []
            lines = output.split('\n')
            for line in lines:
                if line and not line.startswith('-') and not line.startswith('('):
                    module_name = line.strip()
                    if module_name and module_name not in ['name', '']:
                        modules.append(module_name)
            return modules
        return []

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