{
    'name': 'SaaS Billing & Invoicing',
    'version': '18.0.1.0.0',
    'category': 'SaaS',
    'summary': 'Recurring billing, invoices, and dunning for SaaS subscriptions',
    'description': """
        Automates recurring invoice generation for SaaS subscriptions.
        Includes dunning process with email reminders, late fees, and auto-suspension.
        Integrates with Odoo Sales and Accounting modules.
    """,
    'author': 'SaaS Kit',
    'website': 'https://yourdomain.com',
    'depends': ['base', 'saas_subscription', 'sale', 'account', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'data/cron_data.xml',
        'data/mail_template_data.xml',
        'views/saas_billing_views.xml',
        'views/saas_invoice_scheduler_views.xml',
    ],
    'demo': [
        'demo/demo_data.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}