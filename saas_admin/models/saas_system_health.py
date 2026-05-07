from odoo import models, fields, api, _
import subprocess
import logging
from datetime import datetime

try:
    import psutil
except ImportError:
    psutil = None

_logger = logging.getLogger(__name__)


class SaasSystemHealth(models.Model):
    _name = 'saas.system.health'
    _description = 'System Health Monitor'
    _rec_name = 'check_time'
    _order = 'check_time desc'

    check_time = fields.Datetime(string='Check Time', required=True, default=fields.Datetime.now)
    
    # Database stats
    db_count = fields.Integer(string='Total DBs (Tenants)')
    db_size_total = fields.Float(string='Total DB Size (MB)')
    db_size_avg = fields.Float(string='Avg DB Size (MB)')
    
    # Server stats
    cpu_usage = fields.Float(string='CPU Usage (%)')
    memory_usage = fields.Float(string='Memory Usage (%)')
    disk_usage = fields.Float(string='Disk Usage (%)')
    disk_free = fields.Float(string='Disk Free (GB)')
    
    # Application stats
    active_subscriptions = fields.Integer(string='Active Subscriptions')
    pending_provisioning = fields.Integer(string='Pending Provisioning')
    failed_invoices = fields.Integer(string='Failed Invoices')
    
    # Status
    status = fields.Selection([
        ('healthy', 'Healthy'),
        ('warning', 'Warning'),
        ('critical', 'Critical')
    ], string='System Status', default='healthy')
    
    warnings = fields.Text(string='Warnings')

    def action_refresh(self):
        """Refresh health check - called from form button"""
        self.check_system_health()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    @api.model
    def check_system_health(self):
        """Perform comprehensive system health check"""
        
        # Database stats
        db_count = self._get_database_count()
        db_sizes = self._get_database_sizes()
        
        # Server stats
        if psutil:
            cpu_usage = psutil.cpu_percent(interval=1)
            memory_usage = psutil.virtual_memory().percent
            disk_usage = psutil.disk_usage('/').percent
            disk_free = psutil.disk_usage('/').free / (1024**3)  # GB
        else:
            cpu_usage = memory_usage = disk_usage = 0.0
            disk_free = 0.0
        
        # Application stats
        active_subs = self.env['saas.subscription'].search_count([('state', '=', 'active')])
        pending_prov = self.env['tenant.provisioner'].search_count([('state', '=', 'pending')])
        failed_inv = self.env['saas.invoice.scheduler'].search_count([('state', '=', 'failed')])
        
        # Determine status and warnings
        warnings_list = []
        status = 'healthy'
        
        if disk_usage > 85:
            warnings_list.append(f"High disk usage: {disk_usage}%")
            status = 'warning'
        if disk_usage > 95:
            status = 'critical'
        
        if memory_usage > 80:
            warnings_list.append(f"High memory usage: {memory_usage}%")
            status = 'warning' if status != 'critical' else status
        
        if failed_inv > 10:
            warnings_list.append(f"High number of failed invoices: {failed_inv}")
            status = 'warning'
        
        if pending_prov > 5:
            warnings_list.append(f"Provisioning backlog: {pending_prov} pending")
        
        # Create health record
        health_record = self.create({
            'db_count': db_count,
            'db_size_total': sum(db_sizes.values()),
            'db_size_avg': sum(db_sizes.values()) / db_count if db_count > 0 else 0,
            'cpu_usage': cpu_usage,
            'memory_usage': memory_usage,
            'disk_usage': disk_usage,
            'disk_free': disk_free,
            'active_subscriptions': active_subs,
            'pending_provisioning': pending_prov,
            'failed_invoices': failed_inv,
            'status': status,
            'warnings': '\n'.join(warnings_list),
        })
        
        # Send alert if critical
        if status == 'critical':
            self._send_alert(health_record)
        
        return health_record

    def _get_database_count(self):
        """Count number of tenant databases.

        Tenant DBs are named as FQDNs (e.g. abc123.dev.perfecthr.net) — they
        always contain at least one dot, which distinguishes them from the
        master DB and template DB.
        """
        try:
            result = subprocess.run(
                ['psql', '-X', '-tA', '-d', 'postgres', '-c',
                 "SELECT count(*) FROM pg_database "
                 "WHERE datname LIKE '%.%.%' AND datistemplate = false;"],
                capture_output=True, text=True, timeout=15
            )
            return int(result.stdout.strip()) if result.stdout.strip() else 0
        except Exception as e:
            _logger.error(f"Failed to get database count: {e}")
            return 0

    def _get_database_sizes(self):
        """Get sizes of all tenant databases (FQDN-named)."""
        try:
            result = subprocess.run(
                ['psql', '-X', '-tA', '-d', 'postgres', '-c',
                 "SELECT datname, pg_database_size(datname)/1024/1024 "
                 "FROM pg_database "
                 "WHERE datname LIKE '%.%.%' AND datistemplate = false;"],
                capture_output=True, text=True, timeout=15
            )
            sizes = {}
            if result.stdout:
                for line in result.stdout.strip().split('\n'):
                    if '|' in line:
                        parts = line.split('|')
                        db_name = parts[0].strip()
                        size = float(parts[1].strip()) if parts[1].strip() else 0
                        sizes[db_name] = size
            return sizes
        except Exception as e:
            _logger.error(f"Failed to get database sizes: {e}")
            return {}

    def _send_alert(self, health_record):
        """Send critical alert to admins"""
        try:
            template = self.env.ref('saas_admin.email_template_critical_alert', False)
            if template:
                template.send_mail(health_record.id, force_send=True)
        except Exception as e:
            _logger.error(f"Failed to send alert: {e}")

    @api.model
    def _cron_check_health(self):
        """Cron job: Check system health periodically"""
        _logger.info("Running system health check")
        self.check_system_health()
        return True