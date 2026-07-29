# Deployment Runbook

This project is deployable as a **paper-only research dashboard**. It contains no live order, broker, wallet, leverage, futures, private-key, or withdrawal functionality.

## Supported deployment model

Use one container instance behind a TLS-terminating reverse proxy or managed HTTPS platform:

```text
Internet -> HTTPS reverse proxy / platform ingress -> tradebot container:8000
                                                    -> read-only market data
                                                    -> persistent reports/state volume
```

The application stores reports and paper-live state on the local filesystem. Run a single replica unless those files are moved to shared storage in a future version.

## Container security defaults

The supplied image:

- runs as non-root UID/GID `10001`;
- exposes port `8000`;
- enables public binding explicitly inside the container;
- disables all POST mutation endpoints by default;
- uses `/app/data` for read-only market data;
- uses `/var/lib/tradebot/reports` and `/var/lib/tradebot/paper_state` for writable state;
- includes `/health` and `/ready` probes;
- handles `SIGTERM` for graceful shutdown;
- contains no credentials or live-trading endpoints.

## Build locally

```bash
docker build -t tradebot:local .
docker run --rm -p 127.0.0.1:8000:8000 tradebot:local
```

Check it:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

## Docker Compose

```bash
cp .env.example .env
mkdir -p runtime

docker compose up --build -d
docker compose ps
curl http://127.0.0.1:8000/ready
```

By default, Compose publishes only on `127.0.0.1`. Put Caddy, Nginx, Traefik, or a managed HTTPS ingress in front of it instead of exposing the application port directly.

Stop or update:

```bash
docker compose down
git pull
docker compose up --build -d
```

## GitHub Container Registry image

After changes reach `main`, GitHub Actions builds and smoke-tests the image, then publishes:

```text
ghcr.io/sampathkumar-co/crypt-and-share-market:latest
ghcr.io/sampathkumar-co/crypt-and-share-market:sha-<full-commit-sha>
```

For reproducible production deployments, pin the `sha-...` tag rather than `latest`.

Example:

```bash
docker pull ghcr.io/sampathkumar-co/crypt-and-share-market:latest
mkdir -p runtime

docker run -d \
  --name tradebot \
  --restart unless-stopped \
  --read-only \
  --tmpfs /tmp:size=64m,mode=1777 \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  -p 127.0.0.1:8000:8000 \
  -v "$PWD/data:/app/data:ro" \
  -v "$PWD/runtime:/var/lib/tradebot" \
  ghcr.io/sampathkumar-co/crypt-and-share-market:latest
```

## Public read-only deployment

Read-only mode is the recommended public configuration. The dashboard and report GET endpoints are available, but `/run/scan`, `/run/portfolio`, and `/run/robustness` return HTTP `403`.

Required environment values inside a generic platform are:

```text
PORT=8000
TRADEBOT_ALLOW_PUBLIC=true
TRADEBOT_ENABLE_MUTATIONS=false
TRADEBOT_DATA_DIR=/app/data
TRADEBOT_REPORTS_DIR=/var/lib/tradebot/reports
TRADEBOT_STATE_DIR=/var/lib/tradebot/paper_state
```

The container command already uses `0.0.0.0` and reads `PORT`.

## Enabling protected research actions

Do this only for a private/admin deployment. Generate a strong token:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set both:

```text
TRADEBOT_ENABLE_MUTATIONS=true
TRADEBOT_ADMIN_TOKEN=<at-least-32-character-secret>
```

Call a protected action:

```bash
curl -X POST \
  -H "Authorization: Bearer $TRADEBOT_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"folder":"crypto","top":20}' \
  https://your-domain.example/run/scan
```

Never put the admin token in source control, frontend JavaScript, URLs, screenshots, or container image layers. Rotate it if exposed.

## Storage and backups

Persist the whole `/var/lib/tradebot` directory. It contains:

```text
reports/       generated JSON reports
paper_state/   resumable paper-live state
```

The bundled `/app/data` directory is read-only. Mount your own validated OHLCV directory at `/app/data` when needed.

Example backup:

```bash
tar -czf "tradebot-runtime-$(date +%Y%m%d-%H%M%S).tar.gz" runtime/
```

Restore only while the container is stopped.

## Health probes

- `GET /health` is a liveness endpoint.
- `GET /ready` verifies that data is readable and report/state directories are writable.

Recommended platform probe:

```text
Path: /ready
Port: 8000
Initial delay: 10 seconds
Interval: 30 seconds
Timeout: 5 seconds
Failure threshold: 3
```

## Reverse proxy requirements

The application is HTTP-only. Terminate TLS at the platform or reverse proxy and:

- redirect HTTP to HTTPS;
- limit request body size to 64 KiB or less;
- apply reasonable request-rate limits, especially to POST paths;
- preserve the original client IP only through trusted proxy configuration;
- do not cache API or dashboard responses;
- keep the application port private.

## Rollback

Deploy immutable SHA tags. To roll back:

```bash
docker pull ghcr.io/sampathkumar-co/crypt-and-share-market:sha-<previous-sha>
docker stop tradebot
docker rm tradebot
# Start the previous image with the same volume and environment configuration.
```

Back up `runtime/` before schema-changing releases. Version 0.3.0 does not introduce a state migration.

## Production checklist

Before directing traffic to the service:

- CI and Container workflows are green.
- The deployment uses an immutable image tag.
- `/health` and `/ready` return HTTP 200.
- Public deployments have mutations disabled.
- Admin deployments use a newly generated token and HTTPS.
- `/var/lib/tradebot` is on persistent storage and backed up.
- `/app/data` contains validated, non-secret OHLCV data.
- The application port is private behind HTTPS ingress.
- Logging and platform resource alerts are enabled.
- No claim of guaranteed returns appears in surrounding product copy.
