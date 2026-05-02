{
    'name': 'SaaS Customer Portal',
    'version': '18.0.1.0.0',
    'category': 'SaaS',
    'summary': 'Landing page and customer portal for SaaS subscriptions',
    'description': """
        Provides public landing page for package listing and self-subscription.
        Includes customer portal for managing subscriptions, viewing invoices, and redeeming points.
        Features AJAX polling for tenant provisioning status.
    """,
    'author': 'SaaS Kit',
    'website': 'https://yourdomain.com',
    'depends': ['base', 'saas_subscription', 'saas_billing', 'saas_points', 'website', 'auth_signup'],
    'data': [
        'security/ir.model.access.csv',
        'views/portal_templates.xml',
        'views/saas_package_portal_templates.xml',
        'views/saas_subscription_portal_templates.xml',
    ],
    'demo': [],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}