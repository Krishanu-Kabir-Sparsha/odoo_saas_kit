# Odoo CE v18 SaaS Kit - Complete Installation Guide

## Overview
Complete multi-tenant SaaS platform for Odoo CE v18. Includes package management, subscription lifecycle, tenant provisioning, billing, points system, SSLCommerz payment gateway, customer portal, and admin dashboard.

## System Requirements
- Odoo CE v18
- Python 3.10+
- PostgreSQL 15+
- Nginx (for tenant routing)
- SSLCommerz merchant account (for payments)

## Python Dependencies
```bash
pip install cryptography requests psutil
```

## Multi-tenant Routing
For subdomain routing, configure Odoo to map each hostname to its tenant DB:

```
dbfilter = ^%h$
```

Tenant databases are created using the full subdomain (e.g., `tenantid.example.com`) to match this filter. Ensure `saas.domain_base` is set to your base domain.

Add SaaS Pricing to Website's Homepage - 

<t t-call="saas_portal.pricing_block"/>