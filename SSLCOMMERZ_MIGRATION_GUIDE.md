# SSLCommerz Payment Gateway Migration — Complete Documentation

> **Date:** May 5, 2026  
> **Project:** Odoo 18 SaaS Kit  
> **Migration:** Stripe → SSLCommerz  
> **Server:** 203.190.9.169:8069 (dev.perfecthr.net)  
> **Status:** ✅ COMPLETE

---

## Table of Contents

1. [Why SSLCommerz?](#1-why-sslcommerz)
2. [What Was Changed](#2-what-was-changed)
3. [New Module Structure](#3-new-module-structure)
4. [Complete Payment Flow](#4-complete-payment-flow)
5. [Module Installation Order](#5-module-installation-order)
6. [SSLCommerz Configuration](#6-sslcommerz-configuration)
7. [Test Card Numbers](#7-test-card-numbers)
8. [Server Deployment Steps](#8-server-deployment-steps)
9. [Recurring Billing Workflow](#9-recurring-billing-workflow)
10. [API Reference](#10-api-reference)
11. [Bugs Fixed During Migration](#11-bugs-fixed-during-migration)
12. [File-by-File Change Log](#12-file-by-file-change-log)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Why SSLCommerz?

Stripe does not support Bangladesh-based merchants properly. SSLCommerz is the leading payment gateway in Bangladesh, supporting:

- **VISA / MasterCard / AMEX** (all local & international banks)
- **bKash, Nagad, Rocket** (mobile banking)
- **DBBL Nexus, QCash, FastCash** (local cards)
- **Internet Banking** (City Bank, Bank Asia, IBBL, MTBL, etc.)
- **BDT (Bangladeshi Taka)** as primary currency

---

## 2. What Was Changed

### Modules Modified

| Module | What Changed |
|--------|-------------|
| `saas_payment_sslcommerz/` | **NEW** — Complete SSLCommerz payment module (12 files) |
| `saas_subscription` | Removed `stripe_customer_id` & `payment_method_id` fields. Added `payment_gateway` field. Updated button labels. |
| `saas_portal` | Replaced Stripe checkout call with SSLCommerz session. Removed `publishable_key`. Updated branding text. Added dependency on `saas_payment_sslcommerz`. |
| `saas_admin` | Changed dependency from `saas_payment_stripe` → `saas_payment_sslcommerz` |

### What Was Removed

- `stripe_customer_id` field from `saas.subscription` model
- `payment_method_id` field (referenced Stripe's `payment.token`)
- `saas.stripe.publishable_key` system parameter reference
- `create_stripe_checkout_session()` method call
- All "Stripe" branding text from templates and views
- Stripe JS (`js.stripe.com`) — not needed (SSLCommerz uses server-side redirect)

### Old Module (`saas_payment_stripe/`)

> **IMPORTANT:** The old `saas_payment_stripe/` folder still exists in the codebase.  
> It must be **uninstalled from the server** before deletion.  
> See [Section 8: Server Deployment Steps](#8-server-deployment-steps).

---

## 3. New Module Structure

```
saas_payment_sslcommerz/
├── __init__.py                          # Module root import
├── __manifest__.py                      # Module definition
│
├── controllers/
│   ├── __init__.py
│   └── main.py                          # 6 HTTP routes (checkout, IPN, etc.)
│
├── data/
│   └── sslcommerz_config_data.xml       # Default config parameters
│
├── models/
│   ├── __init__.py
│   ├── sslcommerz_config.py             # Config wizard + helpers
│   ├── sslcommerz_transaction.py        # Transaction log + IPN processing
│   └── saas_subscription.py             # Extends saas.subscription
│
├── security/
│   └── ir.model.access.csv              # ACL rules
│
└── views/
    ├── sslcommerz_transaction_views.xml  # Transaction list/form + config wizard
    └── templates.xml                    # Payment result pages (success/fail/cancel)
```

### Key Models

| Model | Purpose |
|-------|---------|
| `sslcommerz.config` | Transient wizard for storing Store ID, Password, Sandbox mode |
| `sslcommerz.transaction` | Persistent log of every payment attempt with full IPN data |
| `saas.subscription` (inherited) | Adds `create_sslcommerz_session()` method |

### Key Routes

| URL | Method | Auth | Purpose |
|-----|--------|------|---------|
| `/saas/payment/checkout` | GET | user | Redirect to SSLCommerz gateway |
| `/saas/payment/success` | GET/POST | public | Success return page |
| `/saas/payment/fail` | GET/POST | public | Failure return page |
| `/saas/payment/cancel` | GET/POST | public | Cancel return page |
| `/saas/sslcommerz/ipn` | POST | none | IPN listener (critical!) |
| `/saas/subscription/<id>/pay_invoice` | GET | user | Pay specific invoice |

---

## 4. Complete Payment Flow

### New Subscription Payment

```
Step 1: Customer visits /saas/packages
Step 2: Customer selects a package → clicks "Get Started"
Step 3: Customer fills signup form (name, email, password)
Step 4: System creates subscription (state=pending) + sale order
Step 5: Customer sees checkout page with order summary
Step 6: Customer clicks "Pay Now"
Step 7: Server calls SSLCommerz Session API (POST /gwprocess/v4/api.php)
Step 8: Server receives GatewayPageURL
Step 9: Customer is redirected to SSLCommerz hosted payment page
Step 10: Customer pays using VISA/bKash/etc.
Step 11: SSLCommerz sends IPN POST to /saas/sslcommerz/ipn
Step 12: Server validates IPN hash signature
Step 13: Server calls Order Validation API to verify amount
Step 14: If valid → subscription activated → tenant provisioning triggered
Step 15: Customer redirected to success page
Step 16: AJAX polling checks provisioning status
Step 17: Customer sees "Instance Ready" with access URL
```

### Recurring Payment (Renewal)

```
Step 1: Cron generates invoice for due subscriptions
Step 2: Email sent to customer with invoice details
Step 3: Customer clicks "Pay Now" in email or portal
Step 4: Same flow as above (Steps 7-17) but purpose='invoice_pay'
Step 5: If not paid within 9 days → auto-suspended by dunning
```

### IPN Processing Logic

```python
IPN POST received
    ├── Find transaction by tran_id
    ├── Validate IPN hash (MD5 verify_sign)
    ├── If status == 'VALID':
    │   ├── Call Order Validation API (GET /validator/api/validationserverAPI.php)
    │   ├── Verify amount matches expected
    │   ├── If validated:
    │   │   ├── Mark transaction as 'validated'
    │   │   ├── Activate subscription (if pending)
    │   │   ├── Register payment on invoice
    │   │   └── Return 200 OK
    │   └── If validation fails:
    │       ├── Mark transaction as 'failed'
    │       └── Return 400
    ├── If status == 'FAILED': Mark failed
    ├── If status == 'CANCELLED': Mark cancelled
    └── If status == 'EXPIRED': Mark expired
```

---

## 5. Module Installation Order

Install modules in this exact order:

```
1. saas_package           → Base package management (main app)
2. saas_subscription      → Subscription lifecycle
3. saas_billing           → Recurring invoicing & dunning
4. saas_points            → Loyalty points system
5. saas_payment_sslcommerz → SSLCommerz payment gateway  ← NEW
6. saas_portal            → Customer portal & checkout
7. saas_admin             → Admin dashboard
```

---

## 6. SSLCommerz Configuration

### Step 1: Get Credentials

- **Sandbox (Testing):** https://developer.sslcommerz.com/registration/
- **Production (Live):** https://signup.sslcommerz.com/register

Upon registration, you receive:
- **Store ID** (e.g., `testbox` for sandbox, `yourstoreid_live` for production)
- **Store Password** (Secret Key)

### Step 2: Configure in Odoo

1. Go to **SaaS → Payment → SSLCommerz Configuration**
2. Enter your Store ID
3. Enter your Store Password
4. Check/uncheck **Sandbox Mode** as appropriate
5. Click **Save and Validate**
6. The system will test your credentials against SSLCommerz API

### Step 3: Configure IPN URL in SSLCommerz Panel

1. Log in to your SSLCommerz merchant panel
2. Go to **IPN Settings** (or **Manage URL** section)
3. Set the IPN URL to:

```
https://dev.perfecthr.net/saas/sslcommerz/ipn
```

> **CRITICAL:** The IPN URL must be publicly accessible from the internet.
> SSLCommerz sends POST requests to this URL to confirm payments.
> Without this, payments will NOT be processed.

### Step 4: Configure Success/Fail/Cancel URLs (Optional)

These are set automatically by the code, but you can also configure them in the SSLCommerz panel:

```
Success URL: https://dev.perfecthr.net/saas/payment/success
Fail URL:    https://dev.perfecthr.net/saas/payment/fail
Cancel URL:  https://dev.perfecthr.net/saas/payment/cancel
```

---

## 7. Test Card Numbers

| Card Type | Card Number | Expiry | CVV |
|-----------|-------------|--------|-----|
| VISA | 4111111111111111 | 12/26 | 111 |
| MasterCard | 5111111111111111 | 12/26 | 111 |
| American Express | 371111111111111 | 12/26 | 111 |
| Mobile OTP | — | — | 111111 or 123456 |

> These only work in **Sandbox mode**. Switch to Production for real payments.

---

## 8. Server Deployment Steps

### On Production Server (203.190.9.169)

```bash
# 1. Pull the latest code
cd /path/to/odoo/addons/odoo_saas_kit
git pull origin main

# 2. Stop Odoo
sudo systemctl stop odoo

# 3. If saas_payment_stripe is installed, uninstall it first
# In Odoo: Settings → Apps → Search "SaaS Stripe" → Uninstall
# Or via shell:
python3 /path/to/odoo-bin shell -d your_db_name
>>> env['ir.module.module'].search([('name','=','saas_payment_stripe')]).button_immediate_uninstall()

# 4. Start Odoo with module update
sudo /path/to/odoo-bin -d your_db_name -u saas_subscription,saas_payment_sslcommerz,saas_portal,saas_admin --stop-after-init

# 5. Start Odoo normally
sudo systemctl start odoo

# 6. Once confirmed working, you can safely delete the old stripe module
rm -rf /path/to/odoo/addons/odoo_saas_kit/saas_payment_stripe/
```

### Migration Checklist

- [ ] Code deployed to server
- [ ] `saas_payment_stripe` uninstalled
- [ ] `saas_payment_sslcommerz` installed
- [ ] All modules updated (`-u all`)
- [ ] SSLCommerz credentials configured in Odoo
- [ ] IPN URL configured in SSLCommerz panel
- [ ] Test payment successful in Sandbox mode
- [ ] Switch to Production mode
- [ ] Test payment successful in Production mode
- [ ] Old `saas_payment_stripe/` folder deleted
- [ ] WORKFLOW_GUIDE documentation updated

---

## 9. Recurring Billing Workflow

### How It Differs from Stripe

| Feature | Stripe | SSLCommerz |
|---------|--------|------------|
| Saved Cards | ✅ Yes — charge automatically | ❌ No — customer must pay each time |
| Recurring Billing | ✅ Native support | ❌ Manual — new session per payment |
| Webhooks | Signed webhooks | IPN + Order Validation API |
| Auto-charge | ✅ Charge saved card | ❌ Send payment link to customer |

### How Recurring Billing Works Now

1. **Cron job** (`_cron_generate_recurring_invoices`) runs daily
2. Finds active subscriptions where `date_next_invoice <= today`
3. Creates invoice + posts it
4. Sends email to customer with payment link
5. Customer clicks link → redirected to SSLCommerz
6. After payment → IPN confirms → invoice marked paid
7. `date_next_invoice` updated to next cycle (30 or 365 days)

### If Customer Doesn't Pay

1. **Day 2:** Dunning reminder 1 sent
2. **Day 5:** Dunning reminder 2 + late fee applied (default 5%)
3. **Day 8:** Final warning sent
4. **Day 9+:** Subscription **auto-suspended**
5. Customer can reactivate by paying the overdue invoice

---

## 10. API Reference

### SSLCommerz Session API

```
POST https://sandbox.sslcommerz.com/gwprocess/v4/api.php
POST https://securepay.sslcommerz.com/gwprocess/v4/api.php  (production)

Required Parameters:
  store_id, store_passwd, total_amount, currency, tran_id,
  success_url, fail_url, cancel_url, ipn_url,
  cus_name, cus_email, cus_add1, cus_city, cus_country, cus_phone,
  product_name, product_category, product_profile, shipping_method

Custom Metadata (value_a through value_d):
  value_a = subscription_id
  value_b = invoice_id (if applicable)
  value_c = partner_id
  value_d = purpose ('checkout', 'renewal', 'invoice_pay')

Response:
  { "status": "SUCCESS", "GatewayPageURL": "https://...", "sessionkey": "..." }
```

### SSLCommerz Order Validation API

```
GET https://sandbox.sslcommerz.com/validator/api/validationserverAPI.php
GET https://securepay.sslcommerz.com/validator/api/validationserverAPI.php  (production)

Parameters: val_id, store_id, store_passwd, format=json

Response:
  { "status": "VALID" | "VALIDATED", "amount": "100.00", "tran_id": "..." }
```

### IPN POST Parameters (from SSLCommerz)

```
status:      VALID | FAILED | CANCELLED | UNATTEMPTED | EXPIRED
tran_id:     Your unique transaction ID
val_id:      SSLCommerz validation ID
amount:      Transaction amount
store_amount: Amount after commission
card_type:   Payment method used
bank_tran_id: Bank transaction reference
verify_sign: MD5 hash for verification
verify_key:  Comma-separated list of fields used in hash
risk_level:  0 (safe) or 1 (risky)
value_a-d:   Your custom metadata
```

---

## 11. Bugs Fixed During Migration

### Bug 1: Broken Invoice Domain Query
**File:** `saas_portal/controllers/portal.py` → `portal_my_invoices()`  
**Issue:** `'|' * (len(domains) - 1) + tuple(domains)` was invalid Python — string * int + tuple concatenation fails  
**Fix:** Rewrote with proper Odoo domain builder using list construction

### Bug 2: Missing Template Variables
**File:** `saas_portal/controllers/portal.py` → `portal_subscription_detail()` and `portal_my_points()`  
**Issue:** Templates referenced `points_value` and `min_points` but controllers never passed them  
**Fix:** Added calculation and passing of both variables

### Bug 3: Template ID Mismatch
**File:** `saas_portal/views/portal_templates.xml`  
**Issue:** Template defined as `portal_my_points_page` but controller rendered `portal_my_points`  
**Fix:** Renamed template ID to match

---

## 12. File-by-File Change Log

### New Files Created (12 files)

| File | Lines | Purpose |
|------|-------|---------|
| `saas_payment_sslcommerz/__init__.py` | 2 | Module imports |
| `saas_payment_sslcommerz/__manifest__.py` | 25 | Module definition |
| `saas_payment_sslcommerz/controllers/__init__.py` | 1 | Controller imports |
| `saas_payment_sslcommerz/controllers/main.py` | 143 | HTTP routes: checkout, IPN, success/fail/cancel |
| `saas_payment_sslcommerz/data/sslcommerz_config_data.xml` | 9 | Default sandbox config |
| `saas_payment_sslcommerz/models/__init__.py` | 3 | Model imports |
| `saas_payment_sslcommerz/models/sslcommerz_config.py` | 125 | Config wizard + helper functions |
| `saas_payment_sslcommerz/models/sslcommerz_transaction.py` | 278 | Transaction model + IPN processing |
| `saas_payment_sslcommerz/models/saas_subscription.py` | 147 | Session creation for subscriptions |
| `saas_payment_sslcommerz/security/ir.model.access.csv` | 4 | ACL rules |
| `saas_payment_sslcommerz/views/sslcommerz_transaction_views.xml` | 142 | List/Form/Config views + menus |
| `saas_payment_sslcommerz/views/templates.xml` | 81 | Payment result portal pages |

### Files Modified (7 files)

| File | Change |
|------|--------|
| `saas_subscription/models/saas_subscription.py` | Removed `stripe_customer_id`, `payment_method_id`. Added `payment_gateway`. Updated docstrings. |
| `saas_subscription/views/saas_subscription_views.xml` | "Pay Now" button, removed stripe field reference |
| `saas_portal/__manifest__.py` | Added `saas_payment_sslcommerz` dependency |
| `saas_portal/controllers/main.py` | Replaced Stripe checkout with SSLCommerz session |
| `saas_portal/controllers/portal.py` | Fixed 3 bugs (domain query, missing variables, template ID) |
| `saas_portal/views/saas_subscription_portal_templates.xml` | "Secure payment via SSLCommerz" |
| `saas_admin/__manifest__.py` | Dependency: `saas_payment_stripe` → `saas_payment_sslcommerz` |

---

## 13. Troubleshooting

### "SSLCommerz is not configured"
→ Go to SaaS → Payment → SSLCommerz Configuration and save your credentials

### IPN not being received
→ Check that IPN URL is publicly accessible: `curl -X POST https://yourdomain.com/saas/sslcommerz/ipn`  
→ Check your SSLCommerz merchant panel IPN settings  
→ Check Odoo logs for IPN-related entries

### "Order validation failed"
→ Check Odoo logs for the validation API response  
→ Ensure `store_id` and `store_passwd` are correct  
→ Verify amount hasn't been tampered with

### Payment succeeds but subscription not activated
→ Check `sslcommerz.transaction` records in Odoo backend  
→ Look at the `ipn_payload` and `validation_payload` fields  
→ Ensure the subscription was in 'pending' state when IPN arrived

### Sandbox vs Production
→ In sandbox: all URLs start with `sandbox.sslcommerz.com`  
→ In production: all URLs start with `securepay.sslcommerz.com`  
→ Toggle via the config wizard or system parameter `saas.sslcommerz.is_sandbox`

### TLS Compatibility
→ Your server must support TLS 1.2+  
→ Test: `curl "https://sandbox.sslcommerz.com/public/tls/" -v`  
→ Expected output: "TLS is okay"

---

> **This document is part of the Odoo SaaS Kit project.**  
> **Keep this file in the project root for reference.**  
> **Last updated: May 5, 2026**
