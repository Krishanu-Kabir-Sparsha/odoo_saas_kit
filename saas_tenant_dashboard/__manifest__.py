{
    'name': 'SaaS Tenant Dashboard',
    'version': '18.0.1.0.1',
    'category': 'Hidden',
    'summary': 'In-tenant "My Subscription & Usage" dashboard',
    'description': """
        Installed automatically on every SaaS tenant database.
        Shows the customer, inside their own workspace:
        - Active package / plan, status, renewal date and days left
        - Storage used (database + attachments) vs the plan quota, with a
          soft-limit warning banner (never blocks work)
        - Active internal users vs the plan's user limit
        - Installed apps
        - Links back to the portal to manage the plan / upgrade / get more storage
        Subscription facts are pushed from the master as a JSON snapshot in
        ir.config_parameter 'saas.subscription_info'; storage usage is computed
        live inside the tenant.
    """,
    'author': 'Perfect Hr',
    'website': 'https://perfecthr.net/',
    'depends': ['base', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'views/tenant_dashboard_views.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': True,
    'license': 'LGPL-3',
}
