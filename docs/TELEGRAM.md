# Telegram Signal Delivery

Telegram is notification only; it has no exchange execution authority.

Canonical `ENTRY_READY` events enter the durable notification outbox. Delivery is at-least-once with leases, retries/rate-limit handling, dead-letter state, and delivery-uncertain handling for expired in-flight leases.

Production activation requires valid `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_SIGNAL_DELIVERY_ENABLED=true`, and a release-scoped `TELEGRAM_SIGNAL_DELIVERY_CUTOVER_AT` boundary. Pre-cutover events are suppressed.

A signal message includes decision/readiness, lifecycle, entry/SL/TP/leverage, evidence summary, blockers/reasons, and AI advisory when available.

Use a read-only Telegram `getMe` probe for credential validation when a message send is not explicitly required. Never log token or chat identifiers.
