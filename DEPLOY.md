# Deployment Guide

Deploy the stock trading system to a cloud VPS with HTTPS.

## Prerequisites

- A VPS (any provider: DigitalOcean, Vultr, AWS Lightsail, etc.)
  - Minimum: 1 vCPU, 1GB RAM, 20GB disk
  - OS: Ubuntu 22.04+ or Debian 12+
- A domain name with DNS A record pointing to the server IP

## Step-by-Step

### 1. Provision VPS & Configure DNS

1. Create a VPS instance
2. Note the server's public IP address
3. In your DNS provider, create an A record:
   ```
   stock.yourdomain.com  →  YOUR_SERVER_IP
   ```
4. Wait for DNS propagation (usually 1-5 minutes)

### 2. SSH into the Server

```bash
ssh root@YOUR_SERVER_IP
```

### 3. Clone the Repository

```bash
cd /opt
git clone https://github.com/YOUR_USER/stock_trading.git
cd stock_trading
```

### 4. Configure Environment

```bash
cp backend/.env.example backend/.env
nano backend/.env
```

Set at minimum:
- `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` for AI features
- `AUTH_USERNAME` and `AUTH_PASSWORD` for login
- `SECRET_KEY` — a random string for session security

### 5. Deploy

```bash
DOMAIN=stock.yourdomain.com EMAIL=you@email.com ./deploy.sh
```

This single command will:
1. Install Docker & Docker Compose (if not present)
2. Obtain a Let's Encrypt SSL certificate
3. Generate the HTTPS nginx config
4. Build and start all containers
5. Set up automatic certificate renewal via cron

### 6. Verify

Open `https://stock.yourdomain.com` in your browser. You should see the login page.

## Architecture

```
Internet → nginx (80/443) → Gunicorn (8000) → Flask App
                                                  ↓
                                              SQLite (./data/)
```

- **nginx**: Reverse proxy, SSL termination, static file serving, SSE streaming
- **Gunicorn**: 2 workers (suitable for single-user SQLite)
- **Flask**: Application server
- **certbot**: Automatic SSL certificate renewal

## Local Development

For local development without HTTPS:

```bash
docker compose up --build
```

This uses `nginx/nginx.conf` (HTTP-only with SSE fix). Visit `http://localhost`.

## Managing the Deployment

```bash
# View logs
docker compose logs -f

# Restart services
docker compose restart

# Rebuild after code changes
docker compose up -d --build

# Stop everything
docker compose down

# Manual cert renewal
certbot renew --quiet
docker compose exec nginx nginx -s reload
```

## Updating

```bash
cd /opt/stock_trading
git pull
docker compose up -d --build
```

## Troubleshooting

**Cert issue**: If `deploy.sh` fails on SSL, ensure port 80 is open and DNS is pointing correctly:
```bash
curl -I http://stock.yourdomain.com
```

**App not starting**: Check logs:
```bash
docker compose logs app
```

**SSE not streaming**: The nginx config includes `proxy_buffering off`. If using a CDN (Cloudflare), disable their buffering or use "Full (strict)" SSL mode.
