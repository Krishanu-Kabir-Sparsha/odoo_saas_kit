from odoo import models, fields, api, _

class SaasPackageFeature(models.Model):
    _name = 'saas.package.feature'
    _description = 'SaaS Package Feature'
    _order = 'sequence, id'

    package_id = fields.Many2one('saas.package', string='Package', required=True, ondelete='cascade')
    module_id = fields.Many2one(
        'ir.module.module',
        string='Module',
        required=True,
        ondelete='cascade',
        help='Select a module from those added to this package'
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

    @api.depends('module_id', 'module_id.shortdesc')
    def _compute_name(self):
        for rec in self:
            rec.name = rec.module_id.shortdesc or rec.module_id.name or ''

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