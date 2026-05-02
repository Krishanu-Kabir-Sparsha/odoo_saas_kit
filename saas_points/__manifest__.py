{
    'name': 'SaaS Points System',
    'version': '18.0.1.0.0',
    'category': 'SaaS',
    'summary': 'Loyalty points system for SaaS subscriptions',
    'description': """
        Customers earn points on paid invoices.
        Points can be redeemed for discounts on future renewals.
        Points expire after configurable period.
        Includes full transaction history and balance tracking.
    """,
    'author': 'SaaS Kit',
    'website': 'https://yourdomain.com',
    'depends': ['base', 'saas_subscription', 'account', 'mail', 'portal'],
    'data': [
        'security/ir.model.access.csv',
        'data/cron_data.xml',
        'data/mail_template_data.xml',
        'views/points_views.xml',
        'views/partner_points_views.xml',
        'wizard/points_redeem_wizard_views.xml',
    ],
    'demo': [
        'demo/demo_data.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}