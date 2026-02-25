#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────────────────
#  Stock Trading System — One-Command Deployment Script
#  Usage: DOMAIN=yourdomain.com EMAIL=you@email.com ./deploy.sh
# ──────────────────────────────────────────────────────────

DOMAIN="${DOMAIN:?'Please set DOMAIN, e.g. DOMAIN=stock.example.com ./deploy.sh'}"
EMAIL="${EMAIL:?'Please set EMAIL for Let'\''s Encrypt, e.g. EMAIL=you@example.com ./deploy.sh'}"

echo "==> Deploying stock-trading to ${DOMAIN}"

# ── 1. Install Docker + Docker Compose if missing ──
if ! command -v docker &>/dev/null; then
    echo "==> Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
fi

if ! docker compose version &>/dev/null; then
    echo "==> Installing Docker Compose plugin..."
    apt-get update && apt-get install -y docker-compose-plugin
fi

# ── 2. Obtain SSL certificate (first time only) ──
CERT_PATH="/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"
if [ ! -f "${CERT_PATH}" ]; then
    echo "==> Obtaining SSL certificate for ${DOMAIN}..."
    # Stop anything on port 80 temporarily
    docker compose down 2>/dev/null || true

    # Install certbot if not present
    if ! command -v certbot &>/dev/null; then
        apt-get update && apt-get install -y certbot
    fi

    certbot certonly --standalone -d "${DOMAIN}" --email "${EMAIL}" --agree-tos --non-interactive

    echo "==> SSL certificate obtained!"
else
    echo "==> SSL certificate already exists, skipping."
fi

# ── 3. Generate nginx config from template ──
echo "==> Generating nginx config for ${DOMAIN}..."
sed "s/__DOMAIN__/${DOMAIN}/g" nginx/nginx.conf.template > nginx/nginx.conf

# ── 4. Copy certbot certs into docker volume ──
# We mount /etc/letsencrypt as a read-only volume in docker-compose,
# so certs are available directly.

# ── 5. Build and start containers ──
echo "==> Building and starting containers..."
docker compose up -d --build

# ── 6. Set up cert renewal cron job ──
CRON_CMD="0 3 * * * certbot renew --quiet && docker compose -f $(pwd)/docker-compose.yml exec nginx nginx -s reload"
if ! crontab -l 2>/dev/null | grep -q "certbot renew"; then
    echo "==> Setting up automatic cert renewal cron job..."
    (crontab -l 2>/dev/null; echo "${CRON_CMD}") | crontab -
    echo "==> Cron job added."
else
    echo "==> Cert renewal cron job already exists, skipping."
fi

echo ""
echo "=========================================="
echo "  Deployment complete!"
echo "  https://${DOMAIN}"
echo "=========================================="
echo ""
echo "Useful commands:"
echo "  docker compose logs -f          # View logs"
echo "  docker compose restart           # Restart services"
echo "  docker compose down              # Stop everything"
echo "  docker compose up -d --build     # Rebuild and start"
