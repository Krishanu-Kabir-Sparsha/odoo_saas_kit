from odoo import models, fields, api, _
from datetime import timedelta
import logging

_logger = logging.getLogger(__name__)

class SaasSubscriptionAutoExpire(models.AbstractModel):
    _name = 'saas.subscription.auto_expire'
    _description = 'Auto Expire Suspended Subscriptions'

    @api.model
    def _auto_cancel_suspended_subscriptions(self):
        """Cron job: Auto-cancel subscriptions suspended for more than 14 days"""
        _logger.info("Running auto-cancel for suspended subscriptions")
        
        cutoff_date = fields.Datetime.now() - timedelta(days=14)
        
        suspended_subs = self.env['saas.subscription'].search([
            ('state', '=', 'suspended'),
            ('date_suspended', '<=', cutoff_date)
        ])
        
        for sub in suspended_subs:
            _logger.info(f"Auto-cancelling suspended subscription {sub.name}")
            sub.write({
                'state': 'canceled',
                'date_end': fields.Date.today(),
                'date_canceled': fields.Datetime.now(),
                'state_reason': 'Auto-canceled after 14 days suspension'
            })
            sub._log_state_change('suspended', 'canceled', 'Auto-canceled by cron job')
        
        _logger.info(f"Auto-canceled {len(suspended_subs)} suspended subscriptions")
        return True