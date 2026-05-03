# Odoo SaaS Kit - Summary Workflow

## Suite Purpose
The suite delivers a complete SaaS lifecycle on Odoo: package definition, customer signup, subscription management, payment collection, tenant provisioning, recurring billing, dunning, loyalty points, customer portal access, and admin operations.

## Module Roles
- saas_package: defines plans, pricing, features, discounts, and selectable Odoo modules.
- saas_subscription: controls the subscription lifecycle from draft to active, suspended, canceled, rejected, and provisioning_failed, and triggers tenant provisioning.
- saas_payment_stripe: handles Stripe checkout, saved payment methods, invoice payments, and webhook processing.
- saas_billing: generates recurring invoices, advances billing dates, runs dunning, and applies late fees.
- saas_points: earns, redeems, and expires loyalty points, with balance tracking and invoice-based rewards.
- saas_portal: exposes public package listing, signup, checkout, provisioning status, and customer self-service pages.
- saas_admin: provides the admin dashboard, bulk actions, system health monitoring, and tenant operational controls.

## End-to-End Workflow
1. Admin creates SaaS packages with pricing, features, discounts, and included modules.
2. Customer visits the public package page, selects a plan, and completes signup.
3. The system creates the partner, user, and draft subscription, then confirms it into pending payment.
4. Customer pays through Stripe checkout or a saved payment method.
5. A successful payment activates the subscription and triggers tenant provisioning.
6. Provisioning creates the tenant database, installs selected modules, configures access, and stores tenant credentials.
7. The billing engine generates recurring invoices on schedule and advances the next invoice date.
8. If invoices stay unpaid, the dunning process sends reminders, applies late fees, and suspends service when required.
9. Paid invoices earn loyalty points; customers can redeem points at checkout for discounts; expired points are removed automatically.
10. Customers manage subscriptions, invoices, and points from the portal.
11. Admins monitor subscription health, system status, provisioning queue, failed invoices, and can force actions when needed.

## Core Data Flow
- Package data feeds subscription creation.
- Subscription state changes drive payment, provisioning, billing, and notifications.
- Stripe webhooks update payment and activation outcomes.
- Billing and dunning depend on invoice status and subscription activity.
- Points are tied to paid invoices and customer balance records.
- Portal pages reflect live subscription, invoice, and points data.
- Admin views consolidate operational monitoring and corrective actions.

## Operational Summary
- Install order matters because later modules extend earlier ones.
- System parameters must be configured for domain, provisioning paths, billing, Stripe, points, and server access.
- Linux server support is required for full tenant provisioning because it depends on PostgreSQL and Nginx commands.
- The suite is designed to support both manual admin control and automated lifecycle processing.

## Quick Read
The suite works as a single SaaS engine: packages define what can be sold, subscriptions control what the customer owns, Stripe handles payment, provisioning creates the tenant, billing keeps it renewed, dunning protects revenue, points improve retention, the portal serves the customer, and the admin dashboard keeps operations under control.
