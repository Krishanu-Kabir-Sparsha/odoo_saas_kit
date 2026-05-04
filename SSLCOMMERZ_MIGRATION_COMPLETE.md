# SSLCommerz Payment Gateway Migration — Complete Report

## ✅ Migration Status: COMPLETE

All Stripe references have been **completely removed** from every module outside the old `saas_payment_stripe/` folder. A new `saas_payment_sslcommerz/` module has been built from scratch.

---

## Summary of All Changes

### 1. New Module Created: `saas_payment_sslcommerz/`

| File | Purpose |
|------|---------|
| `__manifest__.py` | Module definition, depends on `saas_subscription` + `saas_billing` |
| `__init__.py` | Module imports |
| `models/__init__.py` | Model imports |
| `models/sslcommerz_config.py` | Configuration wizard (Store ID, Password, Sandbox toggle) + credential validation + IPN hash verifier |
| `models/sslcommerz_transaction.py` | Transaction log model with full IPN processing, Order Validation API, payment registration, and subscription activation |
| `models/saas_subscription.py` | `_inherit = 'saas.subscription'` — adds `create_sslcommerz_session()` method + transaction tracking fields |
| `controllers/__init__.py` | Controller imports |
| `controllers/main.py` | 6 HTTP routes: checkout redirect, success/fail/cancel pages, IPN listener, invoice payment |
| `security/ir.model.access.csv` | ACL rules for transaction + config models |
| `data/sslcommerz_config_data.xml` | Default sandbox mode config parameter |
| `views/sslcommerz_transaction_views.xml` | List/Form views for transaction log + config wizard + menus |
| `views/templates.xml` | Portal templates for payment success/fail/cancel pages |

### 2. Modified: `saas_subscription/models/saas_subscription.py`

```diff
-    payment_method_id = fields.Many2one('payment.token', string='Saved Payment Method', copy=False)
-    stripe_customer_id = fields.Char(string='Stripe Customer ID', copy=False)
+    payment_gateway = fields.Char(string='Payment Gateway', copy=False, default='sslcommerz',
+                                  help='Payment gateway used for this subscription')
```

```diff
-    def action_pay_now(self):
-        """Redirect to Stripe Checkout payment"""
+    def action_pay_now(self):
+        """Redirect to payment gateway checkout"""
```

### 3. Modified: `saas_subscription/views/saas_subscription_views.xml`

```diff
-    <button name="action_pay_now" ... string="Pay with Stripe"
-            help="Proceed to Stripe Checkout to complete payment"/>
+    <button name="action_pay_now" ... string="Pay Now"
+            help="Proceed to payment gateway to complete payment"/>
```

```diff
-    <field name="payment_method_id"/>
-    <field name="stripe_customer_id"/>
+    <field name="payment_gateway"/>
```

### 4. Modified: `saas_portal/controllers/main.py`

```diff
-    'publishable_key': request.env['ir.config_parameter'].sudo().get_param('saas.stripe.publishable_key', ''),
     (removed — SSLCommerz uses server-side redirect, no client-side JS key needed)
```

```diff
-    checkout_url = subscription.create_stripe_checkout_session(
-        return_url=request.httprequest.url_root
+    gateway_url = subscription.create_sslcommerz_session(
+        return_url=request.httprequest.url_root.rstrip('/')
```

### 5. Modified: `saas_portal/views/saas_subscription_portal_templates.xml`

```diff
-    <small>Secure payment via Stripe. Your data is protected.</small>
+    <small>Secure payment via SSLCommerz. Your data is protected.</small>
```

### 6. Modified: `saas_portal/__manifest__.py`

```diff
-    'depends': [..., 'website', 'auth_signup'],
+    'depends': [..., 'saas_payment_sslcommerz', 'website', 'auth_signup'],
```

### 7. Modified: `saas_admin/__manifest__.py`

```diff
-    'saas_payment_stripe',
+    'saas_payment_sslcommerz',
```

### 8. Fixed: `saas_portal/controllers/portal.py` — 3 Bugs

- **Bug 1**: `portal_my_invoices()` — Domain construction was broken (`'|' * (n-1) + tuple(...)` is invalid Python). Rewrote with proper domain builder.
- **Bug 2**: `portal_subscription_detail()` — Missing `points_value` and `min_points` template variables that templates referenced.
- **Bug 3**: `portal_my_points()` — Same missing variables.

### 9. Fixed: `saas_portal/views/portal_templates.xml`

- Template ID mismatch: `portal_my_points_page` → `portal_my_points` (matching controller's render call)

---

## Complete Payment Flow (SSLCommerz)

```
Customer                 Odoo Server              SSLCommerz
   │                         │                        │
   │ 1. Select Package       │                        │
   ├────────────────────────>│                        │
   │                         │                        │
   │ 2. Fill Signup Form     │                        │
   ├────────────────────────>│                        │
   │                         │ 3. Create Subscription │
   │                         │    (state=pending)     │
   │                         │                        │
   │ 4. Click "Pay Now"      │                        │
   ├────────────────────────>│                        │
   │                         │ 5. POST /gwprocess/v4/ │
   │                         │    api.php             │
   │                         ├───────────────────────>│
   │                         │                        │
   │                         │ 6. Return GatewayURL   │
   │                         │<───────────────────────┤
   │                         │                        │
   │ 7. Redirect to Gateway  │                        │
   │<────────────────────────┤                        │
   │                         │                        │
   │ 8. Customer pays on     │                        │
   │    SSLCommerz page      │                        │
   ├─────────────────────────────────────────────────>│
   │                         │                        │
   │                         │ 9. IPN POST to         │
   │                         │    /saas/sslcommerz/ipn│
   │                         │<───────────────────────┤
   │                         │                        │
   │                         │ 10. Validate hash      │
   │                         │ 11. Call Validation API│
   │                         ├───────────────────────>│
   │                         │<───────────────────────┤
   │                         │                        │
   │                         │ 12. If VALID:          │
   │                         │   - Activate sub       │
   │                         │   - Register payment   │
   │                         │   - Trigger provision  │
   │                         │                        │
   │ 13. Redirect to success │                        │
   │<─────────────────────────────────────────────────┤
   │                         │                        │
   │ 14. Polling activation  │                        │
   │    status via AJAX      │                        │
   ├────────────────────────>│                        │
```

---

## Module Installation Order (Updated)

1. `saas_package` — Base package management
2. `saas_subscription` — Subscription lifecycle
3. `saas_billing` — Recurring invoicing & dunning
4. `saas_points` — Loyalty points system
5. `saas_payment_sslcommerz` — **NEW** Payment gateway ← replaces `saas_payment_stripe`
6. `saas_portal` — Customer-facing portal & checkout
7. `saas_admin` — Admin dashboard

---

## SSLCommerz Configuration Steps

### 1. Get Credentials
- **Sandbox**: Register at https://developer.sslcommerz.com/registration/
- **Production**: Register at https://signup.sslcommerz.com/register

### 2. Configure in Odoo
- Navigate to **SaaS → Payment → SSLCommerz Configuration**
- Enter `Store ID` and `Store Password`
- Toggle **Sandbox Mode** on/off
- Click **Save and Validate** (tests credentials against SSLCommerz API)

### 3. Configure IPN URL in SSLCommerz Panel
- Copy the IPN URL shown in the config form
- Paste it in your SSLCommerz Merchant Panel → IPN Settings
- The URL format is: `https://yourdomain.com/saas/sslcommerz/ipn`

### 4. Test with Sandbox Cards
| Card Type | Number | Exp | CVV |
|-----------|--------|-----|-----|
| VISA | 4111111111111111 | 12/26 | 111 |
| MasterCard | 5111111111111111 | 12/26 | 111 |
| AMEX | 371111111111111 | 12/26 | 111 |
| Mobile OTP | — | — | 111111 |

---

## What to Do with `saas_payment_stripe/`

> [!IMPORTANT]
> **Do NOT delete `saas_payment_stripe/` yet** if it is currently installed on your production server.
> 
> **On the server (203.190.9.169:8069):**
> 1. First uninstall `saas_admin` (it depended on stripe)
> 2. Uninstall `saas_portal` (it used stripe checkout)
> 3. Uninstall `saas_payment_stripe`
> 4. Deploy the new `saas_payment_sslcommerz` module
> 5. Reinstall modules in order: `saas_payment_sslcommerz` → `saas_portal` → `saas_admin`
> 6. Run `-u all` to update all modules
> 7. Only then can you safely delete the `saas_payment_stripe/` folder

---

## Recurring Billing with SSLCommerz

Since SSLCommerz **does not support saved cards or automatic recurring charges** (unlike Stripe), the recurring billing workflow works differently:

1. **Cron generates invoice** (existing `saas_billing` cron — unchanged)
2. **Dunning process sends reminder emails** (existing — unchanged)
3. **Customer clicks "Pay Now" link** in the email or portal
4. **Customer is redirected to SSLCommerz** to pay the invoice
5. **IPN confirms payment** → invoice marked paid, subscription stays active
6. **If not paid within 9 days** → subscription auto-suspended (existing dunning logic — unchanged)

This is the standard approach for SSLCommerz-based SaaS billing in Bangladesh.
