{
    'name': 'SaaS SSLCommerz Payment Gateway',
    'version': '18.0.1.0.0',
    'category': 'SaaS',
    'summary': 'SSLCommerz integration for SaaS payments (Bangladesh)',
    'description': """
        Integrates SSLCommerz payment gateway for SaaS subscriptions.
        Supports one-time payments for new subscriptions and recurring payments for renewals.
        Handles IPN (Instant Payment Notification) for payment confirmation.
        Includes order validation API integration for secure transaction verification.
        Supports VISA, MasterCard, AMEX, bKash, Nagad, Rocket, and more.
    """,
    'author': 'Perfect Hr',
    'website': 'https://perfecthr.net/',
    'depends': ['base', 'saas_subscription', 'saas_billing'],
    'data': [
        'security/ir.model.access.csv',
        'data/sslcommerz_config_data.xml',
        'views/sslcommerz_transaction_views.xml',
        'views/templates.xml',
    ],
    # Note: requests library is required (usually pre-installed with Odoo)
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
