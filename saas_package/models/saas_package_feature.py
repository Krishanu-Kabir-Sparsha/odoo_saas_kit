from odoo import models, fields, api

class SaasPackageFeature(models.Model):
    _name = 'saas.package.feature'
    _description = 'SaaS Package Feature'
    _order = 'sequence, id'

    package_id = fields.Many2one('saas.package', string='Package', required=True, ondelete='cascade')
    name = fields.Char(string='Feature Name', required=True, translate=True)
    description = fields.Text(string='Feature Description', translate=True)
    sequence = fields.Integer(string='Sequence', default=10)
    icon = fields.Char(string='Icon Class', help='FontAwesome icon class (e.g., fa-rocket)')
    
    _sql_constraints = [
        ('unique_feature_per_package', 'unique(package_id, name)', 'Feature name must be unique per package.')
    ]