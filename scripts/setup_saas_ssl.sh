#!/bin/bash
# ============================================================
#  SaaS SSL Complete Setup — perfecthr.net
# ============================================================
#  Run as root: sudo bash setup_saas_ssl.sh
# ============================================================

set -euo pipefail

BASE_DOMAIN="perfecthr.net"
ADMIN_EMAIL="admin@perfecthr.net"
WEBROOT="/var/www/letsencrypt"
NGINX_SITES="/etc/nginx/sites-available"
ODOO_USER="odoo18"
CRON_SCRIPT="/opt/odoo18/fix_tenant_ssl_cron.sh"

echo ""
echo "=========================================="
echo "  SaaS SSL Setup for ${BASE_DOMAIN}"
echo "=========================================="
echo ""

# ── 1. Create ACME webroot ──
echo "[1/5] Creating ACME webroot..."
mkdir -p "${WEBROOT}/.well-known/acme-challenge"
chmod -R 755 "${WEBROOT}"
echo "  ✓ Webroot ready at ${WEBROOT}"

# ── 2. Set up sudoers for Odoo user ──
echo "[2/5] Configuring sudoers for ${ODOO_USER}..."
cat > /etc/sudoers.d/${ODOO_USER}-saas << EOF
${ODOO_USER} ALL=(ALL) NOPASSWD: /usr/bin/certbot *
${ODOO_USER} ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/nginx/sites-available/*
${ODOO_USER} ALL=(ALL) NOPASSWD: /bin/ln -sfn *
${ODOO_USER} ALL=(ALL) NOPASSWD: /usr/sbin/nginx -t
${ODOO_USER} ALL=(ALL) NOPASSWD: /bin/systemctl reload nginx
${ODOO_USER} ALL=(ALL) NOPASSWD: /usr/bin/test *
${ODOO_USER} ALL=(ALL) NOPASSWD: /usr/bin/bash -c *
EOF
chmod 440 /etc/sudoers.d/${ODOO_USER}-saas
echo "  ✓ Sudoers configured"

# ── 3. Fix ALL existing tenants ──
echo "[3/5] Fixing existing tenant SSL..."
FIXED=0
SKIPPED=0
FAILED=0

for conf_file in ${NGINX_SITES}/*.${BASE_DOMAIN}.conf; do
    [ -f "$conf_file" ] || continue
    domain=$(basename "$conf_file" .conf)
    [ "$domain" = "$BASE_DOMAIN" ] && continue

    if grep -q "listen 443 ssl" "$conf_file" 2>/dev/null; then
        echo "  ✓ ${domain} — already has SSL"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    echo "  → Fixing: ${domain}"

    # Ensure ACME location in HTTP config
    if ! grep -q "acme-challenge" "$conf_file" 2>/dev/null; then
        cat > "$conf_file" << HTTPEOF
server {
    listen 80;
    server_name ${domain};
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

    # Get cert
    CERT="/etc/letsencrypt/live/${domain}/fullchain.pem"
    KEY="/etc/letsencrypt/live/${domain}/privkey.pem"
    if [ ! -f "$CERT" ]; then
        if ! certbot certonly --webroot -w "${WEBROOT}" -d "${domain}" \
            --non-interactive --agree-tos -m "${ADMIN_EMAIL}" 2>&1; then
            echo "  ✗ Certbot failed for ${domain}"
            FAILED=$((FAILED + 1))
            continue
        fi
    fi
    [ ! -f "$CERT" ] && FAILED=$((FAILED + 1)) && continue

    # Write HTTPS config
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
    FIXED=$((FIXED + 1))
    echo "  ✓ SSL configured"
done

nginx -t && systemctl reload nginx
echo "  Fixed: ${FIXED} | Skipped: ${SKIPPED} | Failed: ${FAILED}"

# ── 4. Install auto-fix cron ──
echo "[4/5] Installing auto-fix cron job..."
cat > "${CRON_SCRIPT}" << 'CRONEOF'
#!/bin/bash
WEBROOT="/var/www/letsencrypt"
NGINX_SITES="/etc/nginx/sites-available"
ADMIN_EMAIL="admin@perfecthr.net"
BASE_DOMAIN="perfecthr.net"
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
CRONEOF
chmod +x "${CRON_SCRIPT}"
(crontab -l 2>/dev/null | grep -v fix_tenant_ssl; echo "*/5 * * * * ${CRON_SCRIPT}") | crontab -
echo "  ✓ Cron installed (every 5 minutes)"

# ── 5. Verify ──
echo "[5/5] Verifying..."
visudo -c 2>&1 | grep -E "OK|error"
echo "  Certbot: $(sudo -u ${ODOO_USER} sudo -n certbot --version 2>&1)"
echo "  Cron:"
crontab -l | grep fix_tenant
echo ""
echo "=========================================="
echo "  ✓ Setup complete for ${BASE_DOMAIN}!"
echo "=========================================="
echo "  - Existing tenants: fixed"
echo "  - New tenants: auto-SSL via provisioner"
echo "  - Safety net: cron every 5 min"
echo "=========================================="
