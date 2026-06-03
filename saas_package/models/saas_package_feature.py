from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class SaasPackageFeature(models.Model):
    _name = 'saas.package.feature'
    _description = 'SaaS Package Feature'
    _order = 'sequence, id'

    package_id = fields.Many2one('saas.package', string='Package', required=True, ondelete='cascade')
    module_id = fields.Many2one(
        'ir.module.module',
        string='Module',
        ondelete='cascade',
        help='Select a module from those added to this package. '
             'Leave empty and fill in "Custom Text" to show a free-text '
             'feature line instead.'
    )
    custom_label = fields.Char(
        string='Custom Text',
        translate=True,
        help='Free text shown on the pricing card when no module is selected. '
             'No Odoo module is created — this is display-only.'
    )
    name = fields.Char(
        string='Feature Name',
        compute='_compute_name',
        store=True,
        translate=True,
    )
    description = fields.Text(string='Feature Description', translate=True)
    sequence = fields.Integer(string='Sequence', default=10)
    icon = fields.Char(string='Icon Class', help='FontAwesome icon class (e.g., fa-rocket)')

    @api.depends('module_id', 'module_id.shortdesc', 'custom_label')
    def _compute_name(self):
        for rec in self:
            if rec.module_id:
                rec.name = rec.module_id.shortdesc or rec.module_id.name or ''
            else:
                rec.name = rec.custom_label or ''

    @api.constrains('module_id', 'custom_label')
    def _check_module_or_label(self):
        for rec in self:
            if not rec.module_id and not rec.custom_label:
                raise ValidationError(_(
                    'Each feature line needs either a Module or some Custom Text.'
                ))

    @api.onchange('module_id')
    def _onchange_module_id_no_duplicate(self):
        """Prevent picking a module that is already used in another feature row
        of the same package. Works in unsaved edit sessions because onchange
        sees the full in-memory feature_ids set on the parent.
        """
        if not self.module_id:
            return
        package = self.package_id
        if not package:
            return
        siblings = package.feature_ids - self
        duplicate = siblings.filtered(lambda f: f.module_id.id == self.module_id.id)
        if duplicate:
            chosen_name = self.module_id.shortdesc or self.module_id.name
            self.module_id = False
            return {
                'warning': {
                    'title': _('Duplicate Module'),
                    'message': _(
                        '"%s" is already added as a feature for this package. '
                        'Please pick a different module.'
                    ) % chosen_name,
                }
            }

    _sql_constraints = [
        ('unique_module_per_package', 'unique(package_id, module_id)',
         'Each module can only have one feature entry per package.'),
    ]