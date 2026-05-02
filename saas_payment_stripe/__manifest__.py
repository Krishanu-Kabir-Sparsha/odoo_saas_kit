{
    'name': 'SaaS Stripe Payment Gateway',
    'version': '18.0.1.0.0',
    'category': 'SaaS',
    'summary': 'Stripe integration for SaaS payments',
    'description': """
        Integrates Stripe payment gateway for SaaS subscriptions.
        Supports one-time payments for new subscriptions and recurring payments for renewals.
        Handles webhooks for payment confirmation and subscription management.
        Stores Stripe Customer IDs and Payment Method tokens for future charges.
    """,
    'author': 'SaaS Kit',
    'website': 'https://yourdomain.com',
    'depends': ['base', 'saas_subscription', 'saas_billing', 'payment'],
    'data': [
        'security/ir.model.access.csv',
        'data/stripe_config_data.xml',
        'views/res_partner_views.xml',
        'views/stripe_webhook_views.xml',
        'views/templates.xml',  # NEW
    ],
    # Note: stripe library must be installed: pip install stripe
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}