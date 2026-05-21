{
    'name': 'SaaS Tenant Guard',
    'version': '18.0.1.0.0',
    'category': 'Hidden',
    'summary': 'Locks tenant module list to prevent unauthorized installs',
    'description': """
        Installed automatically on SaaS tenant databases.
        - Blocks update_list() so tenants cannot re-discover modules from disk.
        - Blocks module install/uninstall for modules outside the allowed list.
        - Stores the allowed module list in ir.config_parameter 'saas.allowed_modules'.
    """,
    'author': 'Perfect HR SaaS',
    'depends': ['base'],
    'data': [],
    'installable': True,
    'auto_install': False,
    'application': False,
    'license': 'LGPL-3',
}
