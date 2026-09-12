# Operations

## Canonical host paths

- Application checkout: `/srv/waterfallhunter/app`
- Host-owned environment: `/etc/waterfallhunter/waterfallhunter.env`
- Runtime/certification state: `/srv/waterfallhunter/runtime`
- Optional host-owned Compose topology override: `/srv/waterfallhunter/runtime/production-volumes.override.yml`
- Certified backups: `/srv/waterfallhunter/backups`

## Runtime

Docker Compose manages backend, frontend, watchdog, Prometheus, Grafana, and Alertmanager. Nginx is the host edge. `waterfallhunter.service` is intentionally `Type=oneshot` with `RemainAfterExit=yes`; it calls `scripts/production_compose.sh` to assert the canonical stack after boot. Service containers use `restart: unless-stopped`, while `waterfallhunter-healthcheck.timer` invokes a bounded `--recover` health check every minute. Recovery therefore belongs to Docker plus the bounded timer; `Restart=always` on the oneshot unit is not the intended mechanism. The compose wrapper always uses the canonical project name/environment and includes the host-owned topology override only when that file exists.

Useful commands:

```bash
systemctl status waterfallhunter.service
systemctl status waterfallhunter-healthcheck.timer
/srv/waterfallhunter/app/scripts/production_compose.sh ps
journalctl -u waterfallhunter.service -n 200 --no-pager
```

## Health

Backend `/livez` is process liveness. `/api/health`/readiness verifies scanner/hunter/database/runtime progress. Frontend and monitoring services have container healthchecks.

## Safety

Do not use `docker compose down -v`. Persistent database state must survive runtime replacement. `LIVE_TRADING_ENABLED=false` must remain visible in runtime certification.

## GitHub connectivity

Normal host-to-repository operations use the authenticated HTTPS `origin`. A separate `github-ssh` remote may be used as a read-only fallback for fetch verification. Production deployment travels in the opposite direction: GitHub Actions uses the SSH identity and pinned `known_hosts` stored in the GitHub `production` Environment.

Safe smoke checks do not mutate Production:

```bash
cd /srv/waterfallhunter/app
git ls-remote origin refs/heads/main
git ls-remote github-ssh refs/heads/main
systemctl is-enabled waterfallhunter.service waterfallhunter-healthcheck.timer
systemctl is-active waterfallhunter.service waterfallhunter-healthcheck.timer
```

A successful SSH authentication message from GitHub is not shell access; GitHub intentionally refuses interactive shells. Compare the fetched `main` SHA across both remotes when testing fallback continuity.
