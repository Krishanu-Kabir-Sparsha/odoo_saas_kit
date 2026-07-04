# Perfect HR SaaS Suite — Deployment & Configuration SOP (New Server)

> **Server Domain**: `pro.perfecthr.net`
> **Odoo Base Path**: `/opt/odoo/odoo/`
> **Odoo Config**: `/etc/odoo.conf`
> **Odoo User**: `odoo`

---

## Prerequisites Checklist
- [ ] Wildcard DNS configured (`*.pro.perfecthr.net` → pointing to your new server IP).
- [ ] Odoo's Python dependencies installed — see **Step 2** (covers `cryptography`, `lxml`, `psutil`, etc.).

---

## Step 1: Fix Uploaded Modules & Permissions

From your screenshot, you uploaded the modules to a folder named `custom addons`, and the owner is currently `ubuntu`. 

> [!WARNING]
> Odoo and Linux paths **do not play well with spaces**. We strongly recommend renaming `custom addons` to `custom_addons` to prevent bugs. Furthermore, Odoo needs read/write permission, so we must change the owner to `odoo`.

Run these commands on the server:

```bash
# 1. Rename the folder to remove the space
sudo mv "/opt/odoo/odoo/custom addons" /opt/odoo/odoo/custom_addons

# 2. Change ownership to the 'odoo' user so Odoo can read them
sudo chown -R odoo:odoo /opt/odoo/odoo/custom_addons/
```
**Why this is necessary:** Odoo needs to read the python files inside these folders. If the folder is owned by `ubuntu`, the `odoo` service might get a "Permission Denied" error and fail to load your SaaS modules.

---

## Step 2: Install Python Dependencies (Odoo Requirements)

> [!WARNING]
> Do this **before** restarting Odoo (Step 3) or creating any database (Step 6). Odoo will not even start without its Python libraries — a fresh server crashes with `ModuleNotFoundError: No module named 'lxml'` (or a similar missing module) on `import odoo`. While that error exists, **no database can be created** because Odoo dies before it ever reaches PostgreSQL.

This server runs Odoo on the **system Python** (no virtualenv), so Odoo's third-party libraries must be installed into the system Python that `python3` resolves to.

### 1. Install build libraries

A few packages (`psycopg2`, `python-ldap`, `lxml`) compile from source and need these system headers:

```bash
sudo apt update
sudo apt install -y python3-dev build-essential libpq-dev \
    libldap2-dev libsasl2-dev libxml2-dev libxslt1-dev libssl-dev
```

### 2. Install Odoo's Python requirements

Odoo ships its authoritative dependency list at `/opt/odoo/odoo/requirements.txt` (right next to `odoo-bin`). Install everything in one shot:

```bash
sudo pip3 install --break-system-packages -r /opt/odoo/odoo/requirements.txt
```

> [!NOTE]
> **Why `--break-system-packages`?** Ubuntu 24.04 marks the system Python as "externally managed" and blocks `pip3` by default (you'll see `error: externally-managed-environment`). On a server whose only job is Odoo, installing into the system Python is exactly what we want, so this flag tells pip to proceed. The version pins in `requirements.txt` are deliberately matched to Ubuntu 24.04's own packages, so there is nothing for them to conflict with. (On Ubuntu 20.04/22.04 the flag is usually not needed — run the command without it first.)
>
> The standard Odoo 18 `requirements.txt` **already includes** `lxml`, `lxml-html-clean` (required since lxml 5.x split that module out — it's what the `import lxml.html.clean` error needs), and `psutil` (for the admin System-Health dashboard). If you ever use a trimmed requirements file, add them manually:
> ```bash
> sudo pip3 install --break-system-packages lxml_html_clean psutil
> ```

### 3. Verify

```bash
sudo -u odoo python3 -c "import lxml.html.clean; print('lxml OK')"
```

If it prints `lxml OK`, the dependencies are in place and Odoo can start.

**Why this is necessary:** Odoo is built on ~60 third-party Python libraries (lxml, psycopg2, Pillow, Werkzeug, reportlab, …). It does **not** bundle them — it expects them to already exist in the Python environment. The `lxml` crash is just the first missing one; without the full set, both `systemctl restart odoo` (Step 3) and the database-creation commands (Step 6) fail immediately on `import odoo`. Installing `requirements.txt` provides every library at once.

---

## Step 3: Configure odoo.conf

Edit your main Odoo configuration file:

```bash
sudo nano /etc/odoo.conf
```

### Critical Settings to add/modify:

```ini
[options]
; Include the custom_addons directory
addons_path = /opt/odoo/odoo/addons,/opt/odoo/odoo/custom_addons

; ┌──────────────────────────────────────────────────────────┐
; │  THE MOST IMPORTANT SETTING FOR MULTI-TENANT SAAS       │
; └──────────────────────────────────────────────────────────┘
dbfilter = ^%h$

; Reverse proxy mode — required when behind Nginx
proxy_mode = True

; Hide database manager from public access
list_db = False

; Bind only to localhost — Nginx handles public traffic
http_interface = 127.0.0.1
xmlrpc_interface = 127.0.0.1

http_port = 8069
gevent_port = 8072
```
**Why this is necessary:** 
- **`dbfilter`**: This tells Odoo to match the URL (e.g. `tenant1.pro.perfecthr.net`) to the exact database name. This is the core of how the multi-tenant SaaS works.
- **`http_interface = 127.0.0.1`**: This forces Odoo to *only* listen to local connections. We do this because Nginx is acting as our secure front door. We don't want anyone accessing Odoo directly from the internet; all traffic must pass through Nginx first so it can handle the SSL encryption and security.

Restart Odoo:
```bash
sudo systemctl restart odoo
```

---

## Step 4: Configure Sudoers for Tenant Provisioning

The `odoo` user needs passwordless sudo to automatically configure Nginx and SSL for new tenants.

```bash
sudo nano /etc/sudoers.d/odoo-saas
```

Add these exact lines:
```
odoo ALL=(ALL) NOPASSWD: /usr/bin/certbot *
odoo ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/nginx/sites-available/*
odoo ALL=(ALL) NOPASSWD: /bin/ln -sfn *
odoo ALL=(ALL) NOPASSWD: /usr/sbin/nginx -t
odoo ALL=(ALL) NOPASSWD: /bin/systemctl reload nginx
odoo ALL=(ALL) NOPASSWD: /usr/bin/test *
odoo ALL=(ALL) NOPASSWD: /usr/bin/bash -c *
odoo ALL=(ALL) NOPASSWD: /bin/rm -f /etc/nginx/sites-enabled/*
odoo ALL=(ALL) NOPASSWD: /bin/rm -f /etc/nginx/sites-available/*
```

Save and set permissions:
```bash
sudo chmod 440 /etc/sudoers.d/odoo-saas
sudo visudo -c   # Must output "OK"
```

**Why this is necessary:**
When a customer buys a subscription, Odoo automatically creates a new database and a new domain for them. To make the domain work with HTTPS, Odoo needs to run `certbot` (the tool that gets free SSL certificates from Let's Encrypt) and reload Nginx. Normally, only the root administrator can run these commands. The `sudoers` file safely grants the `odoo` user permission to run *only* these specific commands automatically without needing to type a password.

---

## Step 5: Create ACME Webroot & Main Domain Nginx

> [!NOTE]
> Since you already have SSL configured for `pro.perfecthr.net`, you can skip creating the Nginx config and running Certbot for the main domain. **However, you MUST still create the webroot directory**, as Certbot needs it to verify future tenant domains.

### Create ACME Webroot

```bash
sudo mkdir -p /var/www/letsencrypt/.well-known/acme-challenge
sudo chmod -R 755 /var/www/letsencrypt
```
**Why this is necessary:** When Certbot requests an SSL certificate for a new tenant, Let's Encrypt will try to read a secret file from `http://<tenant-domain>/.well-known/acme-challenge/` to verify you own the domain. This command creates the folder where those secret files are temporarily stored.

---

## Step 6: Create Databases

### 1. The Template Database
```bash
sudo -u odoo python3 /opt/odoo/odoo/odoo-bin \
    -c /etc/odoo.conf \
    -d saas_template \
    -i base \
    --without-demo=all \
    --stop-after-init
```
**Why this is necessary:** Installing an Odoo database from scratch takes 3-5 minutes. To make tenant provisioning instant, we create a "blank" template database (`saas_template`). When a customer subscribes, PostgreSQL simply clones this template in 2 seconds, and then installs the requested SaaS apps on top of it.

### 2. The Main Admin Database
```bash
sudo -u odoo python3 /opt/odoo/odoo/odoo-bin \
    -c /etc/odoo.conf \
    -d pro.perfecthr.net \
    -i base,web,mail,saas_package,saas_subscription,saas_billing,saas_admin,saas_portal,saas_points,saas_payment_sslcommerz \
    --without-demo=all \
    --stop-after-init
```
**Why this is necessary:** This creates your primary admin database where you will log in, configure pricing packages, and manage your customers' subscriptions. The database name **must exactly match your domain** (`pro.perfecthr.net`) so Odoo knows to load this database when you visit your website.

---

## Step 7: Set System Parameters

Log into the admin panel at `pro.perfecthr.net` → **Settings → Technical → Parameters → System Parameters**

Set these parameters with exact paths for the new server:

| Key | Exact Value |
|-----|-------------|
| `saas.template_db_name` | `saas_template` |
| `saas.domain_base` | `pro.perfecthr.net` |
| `saas.odoo_bin_path` | `python3 /opt/odoo/odoo/odoo-bin` |
| `saas.odoo_config_path` | `/etc/odoo.conf` |
| `saas.nginx_config_dir` | `/etc/nginx` |
| `saas.acme_webroot` | `/var/www/letsencrypt` |
| `saas.acme_admin_email` | `admin@pro.perfecthr.net` |
| `web.base.url` | `https://pro.perfecthr.net` |

---

## Step 8: Install Full SSL Auto-Fix Cron

Create the file:
```bash
sudo nano /opt/odoo/fix_tenant_ssl.sh
```

Paste this **entire** script:
```bash
#!/bin/bash
WEBROOT="/var/www/letsencrypt"
NGINX_SITES="/etc/nginx/sites-available"
ADMIN_EMAIL="admin@pro.perfecthr.net"
BASE_DOMAIN="pro.perfecthr.net"
CHANGED=0

mkdir -p "${WEBROOT}/.well-known/acme-challenge"

for conf_file in ${NGINX_SITES}/*.${BASE_DOMAIN}.conf; do
    [ -f "$conf_file" ] || continue
    domain=$(basename "$conf_file" .conf)
    [ "$domain" = "$BASE_DOMAIN" ] && continue
    grep -q "listen 443 ssl" "$conf_file" 2>/dev/null && continue

    if ! grep -q "acme-challenge" "$conf_file"; then
        cat > "$conf_file" << HTTPEOF
server {
    listen 80; server_name ${domain};
    location /.well-known/acme-challenge/ { root ${WEBROOT}; allow all; }
    proxy_read_timeout 720s; proxy_connect_timeout 720s; proxy_send_timeout 720s;
    proxy_set_header Host \$host; proxy_set_header X-Forwarded-Host \$host;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme; proxy_set_header X-Real-IP \$remote_addr;
    client_max_body_size 200M;
    location / { proxy_redirect off; proxy_pass http://127.0.0.1:8069; }
    location /longpolling/ { proxy_pass http://127.0.0.1:8072; }
    location /websocket { proxy_pass http://127.0.0.1:8072; proxy_set_header Upgrade \$http_upgrade; proxy_set_header Connection "upgrade"; }
    location ~* /web/static/ { proxy_cache_valid 200 60m; proxy_buffering on; expires 864000; proxy_pass http://127.0.0.1:8069; }
}
HTTPEOF
        nginx -t && systemctl reload nginx
        sleep 2
    fi

    CERT="/etc/letsencrypt/live/${domain}/fullchain.pem"
    KEY="/etc/letsencrypt/live/${domain}/privkey.pem"
    if [ ! -f "$CERT" ]; then
        certbot certonly --webroot -w "${WEBROOT}" -d "${domain}" \
            --non-interactive --agree-tos -m "${ADMIN_EMAIL}" 2>/dev/null || continue
    fi
    [ ! -f "$CERT" ] && continue

    cat > "$conf_file" << SSLEOF
server {
    listen 80; server_name ${domain};
    location /.well-known/acme-challenge/ { root ${WEBROOT}; allow all; }
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl http2; server_name ${domain};
    ssl_certificate ${CERT}; ssl_certificate_key ${KEY};
    ssl_protocols TLSv1.2 TLSv1.3; ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on; ssl_session_cache shared:SSL:10m; ssl_session_timeout 10m;
    proxy_read_timeout 720s; proxy_connect_timeout 720s; proxy_send_timeout 720s;
    proxy_set_header Host \$host; proxy_set_header X-Forwarded-Host \$host;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https; proxy_set_header X-Real-IP \$remote_addr;
    client_max_body_size 200M;
    access_log /var/log/nginx/${domain}_access.log; error_log /var/log/nginx/${domain}_error.log;
    location / { proxy_redirect off; proxy_pass http://127.0.0.1:8069; }
    location /longpolling/ { proxy_pass http://127.0.0.1:8072; }
    location /websocket { proxy_pass http://127.0.0.1:8072; proxy_set_header Upgrade \$http_upgrade; proxy_set_header Connection "upgrade"; proxy_set_header Host \$host; proxy_set_header X-Forwarded-Proto https; }
    location ~* /web/static/ { proxy_cache_valid 200 60m; proxy_buffering on; expires 864000; proxy_pass http://127.0.0.1:8069; }
}
SSLEOF
    CHANGED=1
    logger "fix_tenant_ssl: SSL configured for ${domain}"
done

[ $CHANGED -eq 1 ] && nginx -t && systemctl reload nginx
```

Make it executable and add to cron:
```bash
sudo chmod +x /opt/odoo/fix_tenant_ssl.sh

# Add it to crontab to run every 5 minutes:
(crontab -l 2>/dev/null; echo "*/5 * * * * /opt/odoo/fix_tenant_ssl.sh") | crontab -
```
**Why this is necessary:** When a new tenant is created, the system attempts to get an SSL certificate for them instantly. Sometimes, this fails (e.g. if Let's Encrypt is slow, or DNS hasn't updated). This script runs in the background every 5 minutes. It scans all your tenant domains, and if it finds a tenant without an SSL certificate, it automatically runs Certbot to get one and updates Nginx. It's your ultimate safety net for ensuring all tenants have HTTPS.

---

## Step 9: Create SaaS Packages

In the Odoo Admin panel: **SaaS → Packages → Create**
- Provide Name, Price.
- Select the actual Odoo Modules (e.g. `sale_management`, `crm`) you want included in this package.

## You are ready!
Your multi-tenant SaaS architecture is now completely mapped out and configured for your new `pro.perfecthr.net` server environment.
