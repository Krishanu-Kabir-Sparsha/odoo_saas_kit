{
    'name': 'SaaS Admin Dashboard',
    'version': '18.0.1.0.0',
    'category': 'SaaS',
    'summary': 'Admin dashboard for SaaS management',
    'description': """
        Complete admin dashboard for SaaS platform management.
        Features:
        - Subscription management with force actions
        - System health monitoring
        - Provisioning queue management
        - Failed invoice handling
        - Manual refund processing
        - Real-time tenant status
    """,
    'author': 'SaaS Kit',
    'website': 'https://yourdomain.com',
    'depends': [
        'base', 
        'saas_subscription', 
        'saas_package', 
        'saas_billing',
        'saas_points',
        'saas_payment_sslcommerz',
        'mail',
        'web'
    ],
    'data': [
        'security/saas_security.xml',
        'security/ir.model.access.csv',
        'data/cron_data.xml',
        'views/admin_dashboard_views.xml',
        'views/subscription_admin_views.xml',
        'views/system_health_views.xml',
        'wizard/admin_action_wizard_views.xml',
        'views/menu_views.xml',
    ],
    'demo': [],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}