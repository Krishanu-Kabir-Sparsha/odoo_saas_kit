{
    'name': 'SaaS Package Manager',
    'version': '18.0.1.0.0',
    'category': 'SaaS',
    'summary': 'Manage SaaS packages with module selection',
    'description': """
        Allows admin to create SaaS packages by selecting Odoo modules.
        Includes pricing, discounts, features, and active status tracking.
    """,
    'author': 'Perfect Hr',
    'website': 'https://perfecthr.net/',
    'depends': ['base', 'sale', 'mail'],
    'data': [
        'security/saas_security.xml',
        'security/ir.model.access.csv',
        'views/menu_views.xml',
        'views/saas_package_views.xml',
    ],
    'demo': [
        'demo/demo_data.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}