# Troubleshooting

## No signals

Check Decision Terminal blocker diagnostics first. Distinguish genuine market conditions from systemic-zero gates, stale evidence, cross-exchange disagreement, execution-data unavailability, and anti-chase.

## Dashboard stale

Check backend `/livez`, `/api/health`, SSE/polling status, frontend health, and backend memory. Do not interpret a live transport connection as proof a valid READY snapshot was accepted.

## Telegram silent

Check durable outbox state, delivery enable flag, cutover timestamp, worker health, dead letters/uncertain leases, then use a read-only `getMe` probe. Do not bypass durable delivery with an ad-hoc signal send.

## Database/migration

Stop production mutation on integrity/schema mismatch. Preserve the newest certified backup and migration report. Never run an older runtime against a newer schema without positive compatibility evidence.

## Restart/reboot

Check `systemctl status waterfallhunter.service`, Docker health, and the healthcheck timer. A restart that temporarily masks a recurring fault is not incident closure.
