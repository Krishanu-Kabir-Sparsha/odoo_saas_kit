from odoo import api, models, fields
from datetime import timedelta
import logging

_logger = logging.getLogger(__name__)

class SubscriptionCron(models.AbstractModel):
    _name = 'subscription.cron'
    _description = 'Subscription Cron Jobs'

    @api.model
    def _cron_retry_failed_provisioning(self):
        """Reliable provisioning executor + backstop.

        Runs in the cron worker (not bound by web-request time limits, commits
        its own transaction), so it is the dependable place to provision.
        It is both triggered immediately on activation (via _trigger()) and run
        periodically. It handles three cases:
          1. Provisioner jobs explicitly marked 'failed' (bounded retries).
          2. Jobs stuck in 'provisioning' far longer than any real run — the
             worker died mid-provision; released so they can retry.
          3. Subscriptions that are 'active'/'provisioning_failed' with no
             tenant DB and no job currently running — (re)provisioned here.
        """
        Provisioner = self.env['tenant.provisioner']
        Subscription = self.env['saas.subscription']
        now = fields.Datetime.now()
        stale_cutoff = now - timedelta(minutes=20)
        max_attempts = 3

        # (2) Release jobs stuck in 'provisioning' (worker died mid-run) so
        #     they become eligible for a fresh attempt below.
        stuck = Provisioner.search([
            ('state', '=', 'provisioning'),
            ('started_at', '<', stale_cutoff),
        ])
        if stuck:
            stuck.write({
                'state': 'failed',
                'error_message': 'Auto-released: provisioning exceeded 20 minutes '
                                 '(worker likely recycled mid-run).',
            })
            _logger.warning("Released %d stuck provisioning job(s)", len(stuck))

        # (1)+(3) Any subscription that should have a tenant but doesn't.
        candidates = Subscription.search([
            ('state', 'in', ('active', 'provisioning_failed')),
            ('tenant_db_name', '=', False),
        ])
        done = 0
        for sub in candidates:
            # Skip if a job is actively running for this subscription.
            if Provisioner.search_count([
                ('subscription_id', '=', sub.id),
                ('state', 'in', ('pending', 'provisioning')),
            ]):
                continue
            # Cap total attempts to avoid endless loops on a broken package.
            if Provisioner.search_count([('subscription_id', '=', sub.id)]) >= max_attempts:
                _logger.error(
                    "Subscription %s exceeded %d provisioning attempts — needs "
                    "manual attention.", sub.name, max_attempts)
                continue
            _logger.info("Cron provisioning subscription %s (state=%s)",
                         sub.name, sub.state)
            try:
                Provisioner.sudo().provision_tenant(sub)
                done += 1
            except Exception as e:
                _logger.error("Cron provisioning failed for %s: %s",
                              sub.name, e, exc_info=True)

        if done:
            _logger.info("Cron provisioned %d subscription(s)", done)

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