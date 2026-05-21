"""Override ir.module.module to lock down tenant module management.

When this module is installed on a SaaS tenant database, it:
1. Blocks `update_list()` — prevents Odoo from re-scanning the addons
   path and re-inserting hundreds of module records that the tenant
   should never see.
2. Blocks `button_install` / `button_immediate_install` for modules
   that are NOT in the allowed list stored in
   `ir.config_parameter['saas.allowed_modules']`.
3. Blocks module uninstallation to protect system integrity.

The allowed-module list is written by the provisioner during tenant
creation and is a comma-separated string of technical module names.
"""

import logging

from odoo import api, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class IrModuleModule(models.Model):
    _inherit = 'ir.module.module'

    # ------------------------------------------------------------------
    # 1. Block update_list — prevent module re-discovery from disk
    # ------------------------------------------------------------------
    @api.model
    def update_list(self):
        """Override: Do NOT scan the addons path for new modules.

        In a SaaS tenant, the module catalogue is frozen at provisioning
        time.  Allowing update_list() would re-create all ~700 module
        records from the shared addons directory and defeat the
        per-tenant restriction.
        """
        is_saas_tenant = self.env['ir.config_parameter'].sudo().get_param(
            'saas.tenant_id', False
        )
        if is_saas_tenant:
            _logger.info(
                "SaaS Tenant Guard: update_list() blocked for tenant %s",
                is_saas_tenant,
            )
            # Return tuple (updated_count, added_count) — the format
            # that Odoo 18's base.module.update wizard expects.
            return (0, 0)

        # Non-tenant databases (e.g. the main SaaS admin DB) behave normally.
        return super().update_list()

    # ------------------------------------------------------------------
    # 2. Block install of non-allowed modules
    # ------------------------------------------------------------------
    def _get_allowed_module_names(self):
        """Return the set of module technical names this tenant may use."""
        raw = self.env['ir.config_parameter'].sudo().get_param(
            'saas.allowed_modules', ''
        )
        if not raw:
            return set()
        return set(name.strip() for name in raw.split(',') if name.strip())

    def button_install(self):
        """Override: prevent installation of non-allowed modules."""
        allowed = self._get_allowed_module_names()
        if allowed:
            forbidden = self.filtered(lambda m: m.name not in allowed)
            if forbidden:
                names = ', '.join(forbidden.mapped('shortdesc'))
                raise UserError(_(
                    "Your SaaS plan does not include the following "
                    "module(s): %s\n\n"
                    "Please upgrade your plan to access additional features."
                ) % names)
        return super().button_install()

    def button_immediate_install(self):
        """Override: prevent immediate installation of non-allowed modules."""
        allowed = self._get_allowed_module_names()
        if allowed:
            forbidden = self.filtered(lambda m: m.name not in allowed)
            if forbidden:
                names = ', '.join(forbidden.mapped('shortdesc'))
                raise UserError(_(
                    "Your SaaS plan does not include the following "
                    "module(s): %s\n\n"
                    "Please upgrade your plan to access additional features."
                ) % names)
        return super().button_immediate_install()

    # ------------------------------------------------------------------
    # 3. Block module uninstallation
    # ------------------------------------------------------------------
    def button_uninstall(self):
        """Override: prevent uninstallation in tenant databases."""
        is_saas_tenant = self.env['ir.config_parameter'].sudo().get_param(
            'saas.tenant_id', False
        )
        if is_saas_tenant:
            raise UserError(_(
                "Module uninstallation is not allowed on SaaS instances.\n"
                "Please contact support if you need to modify your setup."
            ))
        return super().button_uninstall()

    def button_immediate_uninstall(self):
        """Override: prevent immediate uninstallation in tenant databases."""
        is_saas_tenant = self.env['ir.config_parameter'].sudo().get_param(
            'saas.tenant_id', False
        )
        if is_saas_tenant:
            raise UserError(_(
                "Module uninstallation is not allowed on SaaS instances.\n"
                "Please contact support if you need to modify your setup."
            ))
        return super().button_immediate_uninstall()
