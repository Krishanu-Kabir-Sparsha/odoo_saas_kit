cat << 'SCRIPT' > /tmp/fix_ssl.sh && sudo bash /tmp/fix_ssl.sh
#!/bin/bash
WEBROOT="/var/www/letsencrypt"
NGINX_SITES="/etc/nginx/sites-available"
ADMIN_EMAIL="admin@perfecthr.net"
BASE_DOMAIN="dev.perfecthr.net"
echo "=== SaaS Tenant SSL Auto-Fix ==="
# Step 1: Create webroot
mkdir -p "${WEBROOT}/.well-known/acme-challenge"
chmod -R 755 "${WEBROOT}"
echo "✓ Webroot ready"
# Step 2: Find and fix all tenant configs
for conf_file in ${NGINX_SITES}/*.${BASE_DOMAIN}.conf; do
    [ -f "$conf_file" ] || continue
    domain=$(basename "$conf_file" .conf)
    [ "$domain" = "$BASE_DOMAIN" ] && continue
    if grep -q "listen 443 ssl" "$conf_file" 2>/dev/null; then
        echo "✓ $domain — already has SSL"
        continue
    fi
    echo "→ Fixing: $domain"
    # Add ACME location if missing
    if ! grep -q "acme-challenge" "$conf_file" 2>/dev/null; then
        cat > "$conf_file" << HTTPEOF
server {
    listen 80;
    server_name ${domain};
    location /.well-known/acme-challenge/ { root ${WEBROOT}; allow all; }
    proxy_read_timeout 720s;
    proxy_connect_timeout 720s;
    proxy_send_timeout 720s;
    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-Host \$host;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Real-IP \$remote_addr;
    client_max_body_size 200M;
    location / { proxy_redirect off; proxy_pass http://127.0.0.1:8069; }
    location /longpolling/ { proxy_pass http://127.0.0.1:8072; }
    location /websocket { proxy_pass http://127.0.0.1:8072; proxy_set_header Upgrade \$http_upgrade; proxy_set_header Connection "upgrade"; }
    location ~* /web/static/ { proxy_cache_valid 200 60m; proxy_buffering on; expires 864000; proxy_pass http://127.0.0.1:8069; }
}
HTTPEOF
        nginx -t && systemctl reload nginx
        sleep 1
    fi
    # Get cert if needed
    CERT="/etc/letsencrypt/live/${domain}/fullchain.pem"
    KEY="/etc/letsencrypt/live/${domain}/privkey.pem"
    if [ ! -f "$CERT" ]; then
        echo "  Requesting certificate..."
        if ! certbot certonly --webroot -w "${WEBROOT}" -d "${domain}" --non-interactive --agree-tos -m "${ADMIN_EMAIL}" 2>&1; then
            echo "  ✗ Certbot failed for $domain"
            continue
        fi
    fi
    [ ! -f "$CERT" ] && echo "  ✗ Cert missing" && continue
    # Write HTTPS config
    cat > "$conf_file" << SSLEOF
server {
    listen 80;
    server_name ${domain};
    location /.well-known/acme-challenge/ { root ${WEBROOT}; allow all; }
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl http2;
    server_name ${domain};
    ssl_certificate ${CERT};
    ssl_certificate_key ${KEY};
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
    location / { proxy_redirect off; proxy_pass http://127.0.0.1:8069; }
    location /longpolling/ { proxy_pass http://127.0.0.1:8072; }
    location /websocket { proxy_pass http://127.0.0.1:8072; proxy_set_header Upgrade \$http_upgrade; proxy_set_header Connection "upgrade"; proxy_set_header Host \$host; proxy_set_header X-Forwarded-Proto https; }
    location ~* /web/static/ { proxy_cache_valid 200 60m; proxy_buffering on; expires 864000; proxy_pass http://127.0.0.1:8069; }
}
SSLEOF
    echo "  ✓ SSL configured for $domain"
done
nginx -t && systemctl reload nginx
echo ""
echo "=== Done! All tenants should now have SSL ==="
SCRIPT