#!/bin/bash
# ============================================================
# fix_tenant_ssl.sh — Automated SSL for ALL SaaS tenants
# ============================================================
# Run this ONCE on your server to:
#   1. Create the ACME webroot directory
#   2. Find all tenant nginx configs (HTTP-only)
#   3. Obtain SSL certs for each via certbot
#   4. Rewrite each config with HTTPS
#
# Usage:
#   sudo bash fix_tenant_ssl.sh
#
# This is safe to re-run — it skips tenants that already have SSL.
# ============================================================

set -euo pipefail

WEBROOT="/var/www/letsencrypt"
NGINX_SITES="/etc/nginx/sites-available"
NGINX_ENABLED="/etc/nginx/sites-enabled"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@perfecthr.net}"
BASE_DOMAIN="${BASE_DOMAIN:-dev.perfecthr.net}"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  SaaS Tenant SSL Auto-Fix Script${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# ── Step 1: Ensure webroot exists ──
echo -e "${YELLOW}[1/4] Creating ACME webroot...${NC}"
mkdir -p "${WEBROOT}/.well-known/acme-challenge"
chmod -R 755 "${WEBROOT}"
echo -e "  ✓ Webroot ready at ${WEBROOT}"
echo ""

# ── Step 2: Find all tenant configs ──
echo -e "${YELLOW}[2/4] Scanning tenant nginx configs...${NC}"

TENANT_CONFIGS=()
SKIP_COUNT=0
FIX_COUNT=0

for conf_file in "${NGINX_SITES}"/*.${BASE_DOMAIN}.conf; do
    [ -f "$conf_file" ] || continue

    domain=$(basename "$conf_file" .conf)

    # Skip the main domain config
    if [ "$domain" = "$BASE_DOMAIN" ]; then
        continue
    fi

    # Check if already has SSL (Phase 2)
    if grep -q "listen 443 ssl" "$conf_file" 2>/dev/null; then
        echo -e "  ✓ ${domain} — already has SSL, skipping"
        SKIP_COUNT=$((SKIP_COUNT + 1))
        continue
    fi

    echo -e "  → ${domain} — needs SSL"
    TENANT_CONFIGS+=("$domain")
done

echo ""
echo -e "  Found ${#TENANT_CONFIGS[@]} tenant(s) needing SSL, ${SKIP_COUNT} already configured"
echo ""

if [ ${#TENANT_CONFIGS[@]} -eq 0 ]; then
    echo -e "${GREEN}All tenants already have SSL! Nothing to do.${NC}"
    exit 0
fi

# ── Step 3: Get certs and update configs ──
echo -e "${YELLOW}[3/4] Obtaining SSL certificates and updating nginx...${NC}"
echo ""

SUCCESS_COUNT=0
FAIL_COUNT=0

for domain in "${TENANT_CONFIGS[@]}"; do
    echo -e "  ─── Processing: ${domain} ───"

    # Ensure HTTP config has .well-known location (for older configs)
    if ! grep -q "acme-challenge" "${NGINX_SITES}/${domain}.conf" 2>/dev/null; then
        echo -e "  ${YELLOW}Adding ACME challenge location to HTTP config...${NC}"
        # Rewrite with the proper HTTP config first
        cat > "/tmp/${domain}.conf" << HTTPCONF
# SaaS Tenant: ${domain}
# Phase 1: HTTP-only (SSL will be added automatically)
server {
    listen 80;
    server_name ${domain};

    location /.well-known/acme-challenge/ {
        root ${WEBROOT};
        allow all;
    }

    proxy_read_timeout 720s;
    proxy_connect_timeout 720s;
    proxy_send_timeout 720s;

    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-Host \$host;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Real-IP \$remote_addr;

    client_max_body_size 200M;

    access_log /var/log/nginx/${domain}_access.log;
    error_log /var/log/nginx/${domain}_error.log;

    location / {
        proxy_redirect off;
        proxy_pass http://127.0.0.1:8069;
    }

    location /longpolling/ {
        proxy_pass http://127.0.0.1:8072;
    }

    location /websocket {
        proxy_pass http://127.0.0.1:8072;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location ~* /web/static/ {
        proxy_cache_valid 200 60m;
        proxy_buffering on;
        expires 864000;
        proxy_pass http://127.0.0.1:8069;
    }
}
HTTPCONF
        cp "/tmp/${domain}.conf" "${NGINX_SITES}/${domain}.conf"
        nginx -t && systemctl reload nginx
        sleep 1
    fi

    # Get SSL certificate
    CERT_PATH="/etc/letsencrypt/live/${domain}/fullchain.pem"
    KEY_PATH="/etc/letsencrypt/live/${domain}/privkey.pem"

    if [ ! -f "$CERT_PATH" ]; then
        echo -e "  Requesting certificate..."
        if certbot certonly --webroot -w "${WEBROOT}" -d "${domain}" \
            --non-interactive --agree-tos -m "${ADMIN_EMAIL}" 2>/dev/null; then
            echo -e "  ${GREEN}✓ Certificate obtained${NC}"
        else
            echo -e "  ${RED}✗ Certbot failed for ${domain}${NC}"
            FAIL_COUNT=$((FAIL_COUNT + 1))
            continue
        fi
    else
        echo -e "  ✓ Certificate already exists"
    fi

    # Verify cert files
    if [ ! -f "$CERT_PATH" ] || [ ! -f "$KEY_PATH" ]; then
        echo -e "  ${RED}✗ Cert files missing after certbot${NC}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        continue
    fi

    # Write HTTPS nginx config
    echo -e "  Writing HTTPS config..."
    cat > "${NGINX_SITES}/${domain}.conf" << HTTPSCONF
# SaaS Tenant: ${domain}
# Phase 2: HTTPS active (auto-generated by fix_tenant_ssl.sh)
server {
    listen 80;
    server_name ${domain};

    location /.well-known/acme-challenge/ {
        root ${WEBROOT};
        allow all;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name ${domain};

    ssl_certificate ${CERT_PATH};
    ssl_certificate_key ${KEY_PATH};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    proxy_read_timeout 720s;
    proxy_connect_timeout 720s;
    proxy_send_timeout 720s;

    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-Host \$host;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Real-IP \$remote_addr;

    client_max_body_size 200M;

    access_log /var/log/nginx/${domain}_access.log;
    error_log /var/log/nginx/${domain}_error.log;

    location / {
        proxy_redirect off;
        proxy_pass http://127.0.0.1:8069;
    }

    location /longpolling/ {
        proxy_pass http://127.0.0.1:8072;
    }

    location /websocket {
        proxy_pass http://127.0.0.1:8072;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }

    location ~* /web/static/ {
        proxy_cache_valid 200 60m;
        proxy_buffering on;
        expires 864000;
        proxy_pass http://127.0.0.1:8069;
    }
}
HTTPSCONF

    echo -e "  ${GREEN}✓ HTTPS config written${NC}"
    SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    echo ""
done

# ── Step 4: Reload nginx ──
echo -e "${YELLOW}[4/4] Testing and reloading nginx...${NC}"
if nginx -t 2>/dev/null; then
    systemctl reload nginx
    echo -e "  ${GREEN}✓ Nginx reloaded successfully${NC}"
else
    echo -e "  ${RED}✗ Nginx config test failed! Check manually with: nginx -t${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  SSL Fix Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "  ✓ Fixed:   ${SUCCESS_COUNT} tenant(s)"
echo -e "  ✓ Skipped: ${SKIP_COUNT} (already had SSL)"
if [ $FAIL_COUNT -gt 0 ]; then
    echo -e "  ${RED}✗ Failed:  ${FAIL_COUNT} tenant(s)${NC}"
fi
echo ""
echo -e "  Future tenants will get SSL automatically"
echo -e "  via the Odoo provisioner (webroot at ${WEBROOT})"
echo -e "${GREEN}========================================${NC}"
