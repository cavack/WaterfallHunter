# Notification delivery runbook

This runbook covers the signal-only durable outbox. A signal transaction creates
the internal event exactly once. External delivery is at-least-once and is never
allowed to roll back or mutate the signal decision.

## Activation boundary

The worker is library-only and fail-closed by default. Do not wire or enable a
real Telegram transport without a separate owner approval that names the target
environment and chat. Tests must use a fake transport. Never log bot tokens,
chat identifiers, raw credentials, or secret-bearing transport responses.

## States

- `PENDING`: ready for first claim when `available_at` is reached.
- `SENDING`: leased to one worker. Another worker cannot claim it.
- `RETRY_WAIT`: transient failure or 429; wait until `available_at`.
- `DELIVERED`: terminal successful delivery.
- `DEAD_LETTER`: terminal permanent failure or exhausted attempts.
- `DELIVERY_UNCERTAIN`: a lease expired after sending may have started. Do not
  automatically retry because the remote service may already have accepted it.

## Alerts and first response

| Alert | Trigger | Required response |
|---|---|---|
| `NOTIFICATION_QUEUE_LAG_HIGH` | oldest active item exceeds 300 seconds | Check worker availability, DB locks, transport rate limits, and queue growth. |
| `NOTIFICATION_DEAD_LETTER_PRESENT` | one or more dead letters | Inspect the sanitized error code and payload hash; fix the cause before an owner-approved replay. |
| `NOTIFICATION_DELIVERY_UNCERTAIN_PRESENT` | one or more expired sending leases | Reconcile with the external provider using event/idempotency metadata; never blind-retry. |

## Recovery procedure

1. Keep live delivery disabled while investigating repeated failures.
2. Record counts by state, oldest pending age, event IDs, payload hashes, and
   deployment revision. Do not copy secrets into the incident record.
3. For an uncertain event, verify external acceptance first. Marking or replaying
   it requires a separately reviewed operator action.
4. For a dead letter, reproduce with a fake transport, fix and test the failure,
   then request explicit replay approval.
5. Confirm the source signal, decision, and immutable outbox payload were not
   altered. Delivery failures must not change lifecycle, ranking, or eligibility.

## Fault-injection coverage

Automated tests cover successful delivery, 429 retry timing, timeout exhaustion,
concurrent lease exclusion, crash/lease-expiry uncertainty, queue-lag alerts, and
preservation of the original signal transaction after delivery failure.
