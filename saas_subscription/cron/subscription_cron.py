from odoo import api, models, fields
from datetime import timedelta
import logging

_logger = logging.getLogger(__name__)

class SubscriptionCron(models.AbstractModel):
    _name = 'subscription.cron'
    _description = 'Subscription Cron Jobs'

    @api.model
    def _cron_retry_failed_provisioning(self):
        """Retry failed provisioning jobs"""
        _logger.info("Running retry for failed provisioning")
        
        failed_jobs = self.env['tenant.provisioner'].search([
            ('state', '=', 'failed'),
            ('attempt_count', '<', 3)
        ])
        
        for job in failed_jobs:
            subscription = job.subscription_id
            if subscription.state == 'provisioning_failed':
                _logger.info(f"Retrying provisioning for {subscription.name}")
                job.sudo().provision_tenant(subscription)
        
        _logger.info(f"Retried {len(failed_jobs)} failed provisioning jobs")

    @api.model
    def _cron_cleanup_old_logs(self):
        """Delete log entries older than 90 days"""
        _logger.info("Running log cleanup")
        
        cutoff_date = fields.Datetime.now() - timedelta(days=90)
        old_logs = self.env['saas.subscription.log'].search([
            ('timestamp', '<', cutoff_date)
        ])
        
        count = len(old_logs)
        old_logs.unlink()
        _logger.info(f"Deleted {count} old log entries")