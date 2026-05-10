from odoo import models, fields, api

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

    _sql_constraints = [
        ('unique_module_per_package', 'unique(package_id, module_id)',
         'Each module can only have one feature entry per package.'),
    ]