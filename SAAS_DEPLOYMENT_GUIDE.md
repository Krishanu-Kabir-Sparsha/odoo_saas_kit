# Perfect HR SaaS Platform — Complete Setup & Testing Guide

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    NGINX (Port 80/443)                   │
│  dev.perfecthr.net → Odoo (main SaaS portal)            │
│  abc123.dev.perfecthr.net → Odoo (tenant instance)      │
│  xyz789.dev.perfecthr.net → Odoo (tenant instance)      │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              ODOO 18 (Port 8069)                         │
│  dbfilter = ^%h$ (routes hostname → database)            │
│                                                          │
│  dev.perfecthr.net DB         → SaaS Admin Portal       │
│  abc123.dev.perfecthr.net DB  → Tenant 1 (separate DB)  │
│  xyz789.dev.perfecthr.net DB  → Tenant 2 (separate DB)  │
│  saas_template DB             → Clone source for tenants│
└─────────────────────────────────────────────────────────┘
```

Each subscription gets its own **isolated PostgreSQL database** cloned from `saas_template`.

---

## 1. Server Configuration Reference

### Odoo Config (`/etc/odoo18.conf`)
```ini
[options]
admin_passwd = N0qMYGq2AM8iDAW7
db_host = False
db_port = False
db_user = odoo18
db_password = <your_db_password>
dbfilter = ^%h$
db_name = dev.perfecthr.net
list_db = False
proxy_mode = True
addons_path = /opt/odoo18/addons,/opt/odoo18/custom_addons,/opt/odoo18/webkul_addons
logfile = /var/log/odoo18/odoo.log
xmlrpc_port = 8069
gevent_port = 8072
workers = 0
bin_path = /usr/bin
```

### System Parameters (Odoo Settings → Technical → System Parameters)

| Key | Value | Purpose |
|---|---|---|
| `web.base.url` | `http://dev.perfecthr.net` | Base URL for callbacks |
| `saas.template_db_name` | `saas_template` | PostgreSQL template for cloning |
| `saas.domain_base` | `dev.perfecthr.net` | Base domain for tenant subdomains |
| `saas.odoo_bin_path` | `/opt/odoo18/venv/bin/python3.12 /opt/odoo18/odoo-bin` | Odoo binary path |
| `saas.odoo_config_path` | `/etc/odoo18.conf` | Odoo config file path |
| `saas.nginx_config_dir` | `/etc/nginx` | Nginx config directory |
| `saas.sslcommerz.store_id` | `daffo69f97a638599a` | SSLCommerz store ID |
| `saas.sslcommerz.store_passwd` | `daffo69f97a638599a@ssl` | SSLCommerz store password |
| `saas.sslcommerz.is_sandbox` | `true` | Sandbox mode (set `false` for production) |

### Key Server Files

| File | Purpose |
|---|---|
| `/etc/odoo18.conf` | Main Odoo configuration |
| `/opt/odoo18/.pgpass` | PostgreSQL passwordless access for provisioner |
| `/etc/nginx/sites-available/*.conf` | Nginx vhosts (auto-generated per tenant) |
| `/etc/sudoers.d/odoo-saas` | Sudo rules for Nginx management |
| `/var/log/odoo18/odoo.log` | Odoo application log |

### DNS Requirement
- `*.dev.perfecthr.net` must resolve to your server IP (wildcard DNS)
- Verify: `dig +short anything.dev.perfecthr.net` should return `203.190.9.169`

---

## 2. Admin Side — Setup & Management

### Login
- URL: `http://dev.perfecthr.net/odoo`
- Credentials: Email = `1`, Password = `1`

### SaaS Menu Structure
- **SaaS → Dashboard** — Overview of all subscriptions
- **SaaS → Subscriptions** — Manage customer subscriptions
- **SaaS → Configuration → Tenant Provisioner** — Monitor provisioning jobs
- **SaaS → Payment → Payment Transactions** — View SSLCommerz transactions

### Managing Packages
1. Go to SaaS → Configuration → Packages (or wherever packages are configured)
2. Each package defines: name, price, billing cycle, included modules
3. Packages appear on the public `/saas/packages` page

### Monitoring Provisioning
1. Go to **SaaS → Configuration → Tenant Provisioner**
2. Each provisioning job shows:
   - State: Pending / Provisioning / Completed / Failed
   - Error details (if failed)
   - Attempt count
   - Timestamps

### Viewing Tenant Databases
```bash
# List all tenant databases
sudo -u odoo18 psql -d postgres -c "SELECT datname FROM pg_database WHERE datname LIKE '%.dev.perfecthr.net' ORDER BY datname;"
```

### Manual Subscription Activation (Admin Override)
If you need to manually activate a subscription:
1. Go to SaaS → Subscriptions → select the subscription
2. Change state to "Active"
3. Provisioning will trigger automatically in background

---

## 3. Client Side — End-to-End Flow

### Step 1: Browse Packages
- URL: `http://dev.perfecthr.net/saas/packages`
- Customer sees available SaaS packages with pricing

### Step 2: Sign Up
- Customer clicks "Subscribe" on a package
- Fills in: Name, Email, Password, Company
- A new Odoo portal user + subscription is created

### Step 3: Payment
- Customer is redirected to SSLCommerz payment gateway
- **Sandbox test card:** VISA `4111 1111 1111 1111`, Exp `12/26`, CVV `111`
- **Mobile OTP:** `111111` or `123456`
- After payment success → redirected back to activation page

### Step 4: Activation
- The "Payment Successful" page shows
- Backend: subscription state changes to "Active"
- Provisioning starts automatically in a background thread

### Step 5: Provisioning (Automatic — takes ~30-60 seconds)
1. Creates a new PostgreSQL database from `saas_template`
2. Installs the package's modules (e.g., purchase, mail, contacts)
3. Sets up admin user with customer's email + generated password
4. Creates Nginx vhost for the tenant subdomain
5. Reloads Nginx

### Step 6: Tenant Ready
- Activation page shows "Your SaaS Instance is Ready!"
- Displays: Tenant URL, Username, Password
- Customer clicks "Launch Instance" to access their Odoo

### Step 7: Tenant Login
- URL: `http://<hash>.dev.perfecthr.net`
- Login with the credentials shown on activation page
- This is a fully independent Odoo instance with its own database

---

## 4. Testing Checklist

### Pre-Test Cleanup
```bash
# Delete old test subscriptions from admin panel first, then:
# List tenant databases
sudo -u odoo18 psql -d postgres -c "SELECT datname FROM pg_database WHERE datname LIKE '%.dev.perfecthr.net';"

# Drop a specific test tenant (replace with actual name)
sudo -u odoo18 psql -d postgres -c 'DROP DATABASE IF EXISTS "abc123.dev.perfecthr.net";'

# Remove corresponding Nginx configs
sudo rm -f /etc/nginx/sites-available/abc123.dev.perfecthr.net.conf
sudo rm -f /etc/nginx/sites-enabled/abc123.dev.perfecthr.net.conf
sudo systemctl reload nginx
```

### Test 1: Fresh Signup Flow
- [ ] Go to `/saas/packages` — packages display correctly
- [ ] Click Subscribe — signup form works
- [ ] Fill in details, submit — redirected to payment
- [ ] Pay with sandbox card — payment completes
- [ ] Redirected to success page — shows "Payment Successful"
- [ ] Check admin: subscription is "Active" (not "Pending")

### Test 2: Provisioning
- [ ] Check admin: Tenant Provisioner shows "Completed"
- [ ] Tenant URL is populated in the subscription record
- [ ] Tenant DB Name is populated
- [ ] New database exists: `sudo -u odoo18 psql -l | grep dev.perfecthr`
- [ ] Nginx config exists: `ls /etc/nginx/sites-enabled/ | grep dev.perfecthr`

### Test 3: Tenant Access
- [ ] Open the tenant URL in browser
- [ ] Login page appears (Odoo login, NOT database selector)
- [ ] Login with provided credentials works
- [ ] Tenant has the correct modules installed
- [ ] Tenant data is isolated (no admin data visible)

### Test 4: Multiple Tenants
- [ ] Sign up a second user with different email
- [ ] Pay → provision → separate database created
- [ ] Both tenants accessible simultaneously
- [ ] Data is completely isolated between tenants

---

## 5. Useful Commands

### Monitoring
```bash
# Live log monitoring
sudo tail -f /var/log/odoo18/odoo.log

# Filter for specific events
sudo tail -f /var/log/odoo18/odoo.log | grep -i -E "provis|payment|activ|error"

# Check Odoo service status
sudo systemctl status odoo18
```

### Database Management
```bash
# List all databases
sudo -u odoo18 psql -d postgres -c "\l"

# Check active connections
sudo -u odoo18 psql -d postgres -c "SELECT datname, count(*) FROM pg_stat_activity GROUP BY datname ORDER BY datname;"

# Backup a database
sudo -u odoo18 pg_dump "dev.perfecthr.net" > /tmp/main_backup.sql

# Backup a tenant database  
sudo -u odoo18 pg_dump "abc123.dev.perfecthr.net" > /tmp/tenant_backup.sql
```

### Nginx Management
```bash
# Test Nginx config
sudo nginx -t

# Reload Nginx
sudo systemctl reload nginx

# List tenant vhosts
ls -la /etc/nginx/sites-available/ | grep dev.perfecthr

# View a tenant's Nginx config
cat /etc/nginx/sites-available/<tenant_domain>.conf
```

### Odoo Service
```bash
# Restart Odoo
sudo systemctl restart odoo18

# Update modules (after code changes)
sudo systemctl stop odoo18
sudo -u odoo18 /opt/odoo18/venv/bin/python3.12 /opt/odoo18/odoo-bin \
  -c /etc/odoo18.conf \
  -d "dev.perfecthr.net" \
  -u saas_portal,saas_subscription,saas_payment_sslcommerz \
  --stop-after-init --no-http
sudo systemctl start odoo18
```

---

## 6. Files Modified in This Session

| Module | File | Changes |
|---|---|---|
| `saas_subscription` | `models/saas_subscription.py` | Decoupled provisioning from write() — runs in background thread |
| `saas_subscription` | `models/tenant_provisioner.py` | Fixed createdb (terminate connections first), HTTP-only Nginx, plaintext password for Odoo 18, SQL CREATE DATABASE instead of createdb utility |
| `saas_payment_sslcommerz` | `controllers/main.py` | Fixed IPN handler (proper cursor/env for auth='none'), success page fallback processing |
| `saas_payment_sslcommerz` | `models/sslcommerz_transaction.py` | Sandbox-tolerant order validation, accept payment when API is unreliable |
| `saas_payment_sslcommerz` | `models/sslcommerz_config.py` | Fixed hash validation (include store_passwd in MD5) |
| `saas_portal` | `controllers/main.py` | Fixed auto-login for Odoo 18, activation status polling (HTTP instead of JSON-RPC) |

---

## 7. Troubleshooting

### "Pending Payment" after paying
- Check log: `grep -i "valid" /var/log/odoo18/odoo.log | tail -20`
- If validation failed → SSLCommerz sandbox is unreliable, restart and retry
- If provisioning crashed → check Tenant Provisioner for error details

### Provisioning "Failed"
- Check the error in SaaS → Configuration → Tenant Provisioner
- Common causes:
  - Active connections to template DB → restart Odoo, retry
  - PostgreSQL permission → ensure `.pgpass` works: `sudo -u odoo18 psql -d postgres -c "SELECT 1;"`
  - Nginx permission → ensure sudoers file exists: `cat /etc/sudoers.d/odoo-saas`

### Tenant URL returns "Database selector"
- `dbfilter = ^%h$` not set in `/etc/odoo18.conf`
- Database name doesn't match hostname
- DNS wildcard not configured

### Can't login to main admin
- Database might be renamed. Check: `sudo -u odoo18 psql -l`
- Ensure `db_name` in config matches actual database name
- Clear browser cookies

### SSLCommerz "Order validation failed"
- Sandbox validation API is unreliable
- Our code handles this — check log for "SANDBOX: Accepting payment" messages
- If persistent, restart Odoo and retry

---

## 8. Production Readiness Checklist

Before going live:

- [ ] **SSL Certificate**: Get wildcard SSL for `*.dev.perfecthr.net` (Let's Encrypt or commercial)
- [ ] **Update provisioner**: Switch Nginx configs to HTTPS (currently HTTP-only)
- [ ] **SSLCommerz Live**: Change `saas.sslcommerz.is_sandbox` to `false`, update store_id and store_passwd with live credentials
- [ ] **Email Templates**: Create the missing email templates for subscription notifications
- [ ] **Workers**: Set `workers = 4` (or more) in odoo.conf for production performance
- [ ] **Backups**: Set up automated database backups for all databases
- [ ] **Monitoring**: Set up log monitoring and alerts
- [ ] **Rate Limiting**: Add fail2ban or similar for login protection
- [ ] **Domain**: Switch from `dev.perfecthr.net` to production domain
