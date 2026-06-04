{
    'name': 'SaaS Subscription Manager',
    'version': '18.0.1.0.0',
    'category': 'SaaS',
    'summary': 'Manage subscription lifecycle for SaaS tenants',
    'description': """
        Manages subscription states: draft, pending, active, suspended, canceled, rejected, provisioning_failed.
        Handles tenant provisioning triggers and state transitions with full audit logging.
        Includes automated PostgreSQL tenant creation, module installation, and Nginx configuration.
    """,
    'author': 'Perfect Hr',
    'website': 'https://perfecthr.net/',
    'depends': ['base', 'saas_package', 'sale', 'mail', 'account'],
    'data': [
        'security/saas_security.xml',
        'security/ir.model.access.csv',
        'data/sequence_data.xml',
        'data/mail_template_data.xml',
        'views/saas_subscription_views.xml',
        'views/saas_subscription_log_views.xml',
        'views/menu_views.xml',
        'views/tenant_provisioner_views.xml',
        'wizard/subscription_wizard_views.xml',
        'data/cron_data.xml',
    ],
    'demo': [
        'demo/demo_data.xml',
    ],
    'external_dependencies': {
        'python': ['cryptography']
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}