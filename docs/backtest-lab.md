# Backtest Lab contract

Backtest Lab is a bounded, deterministic research surface over the canonical
simulated portfolio replay. It is not a signal generator, a probability model, a
promotion gate, or an order path.

## Safety invariants

- `execution_mode` is always `SIGNAL_ONLY`.
- `RiskPolicy.v1()` is selected by the server. The request has no risk-policy
  field and unknown fields are rejected.
- `strategy_equivalent`, `claims_allowed`, and `promotion_allowed` are always
  `false`.
- Every bundle must carry an HMAC-SHA256 attestation created with the
  operator-held `BACKTEST_ARTIFACT_HMAC_KEY`. The API returns 503 when the key
  is not configured and 422 for an invalid or tampered attestation.
- Replay performs no database write and does not update lifecycle, ranking,
  alerts, notifications, or execution state.
- Missing execution evidence is not synthesized. An `OPEN` event must carry a
  complete, hash-bound simulated execution plan produced by the canonical planner.
- The dataset manifest is a 64-character lowercase SHA-256 identity. The API
  validates its shape; the operator remains responsible for independently
  proving the dataset contents and provenance represented by that identity.
- Each request is bounded to 5,000 portfolio events, 5,000 signal-level rows,
  and 10 MB of normalized processing material.

## Endpoints

`GET /api/backtest-lab/contract` returns the canonical risk policy, limits, and
the fixed safety flags. Query parameters are rejected.

`POST /api/backtest-lab/replay` accepts:

```json
{
  "contract_version": "backtest_lab_request_v1",
  "artifact_key_id": "wfh-backtest-hmac-v1",
  "artifact_hmac_sha256": "<server-verifiable-hmac-sha256>",
  "dataset_manifest_hash": "<lowercase-sha256>",
  "initial_equity": 1000,
  "events": [],
  "signal_rows": []
}
```

Portfolio event order is deterministic: timestamp, canonical event-type
priority, then event ID. Event IDs must be unique. `MARK` and `CLOSE` require an
explicit modeled exit cost; `FUNDING` requires a signed amount; `OPEN` requires
signal/cluster identity and a simulated-ready execution plan.

The response contains two deliberately separate reports:

- `portfolio_report`: portfolio-realizable replay including event drilldown,
  equity, maximum drawdown, capacity rejection, open/closed positions,
  partial/rejected fills, entry and exit costs, and net funding.
- `signal_level_report`: ordered observational signal rows with
  `portfolio_realizability_applied=false`.

Both reports are bound to the submitted dataset manifest. The portfolio report
also contains the canonical risk-policy hash and a deterministic replay hash.
These hashes establish content identity, not a digital signature or a claim of
profitability.

## Dashboard workflow

1. Prepare an independently generated unsigned bundle containing `events`,
   optional `signal_rows`, `dataset_manifest_hash`, and `initial_equity`.
2. On an authorized operator environment, attest it without printing the key:

   ```bash
   PYTHONPATH=backend/src:. python scripts/sign_backtest_bundle.py \
     --input unsigned.json --output signed.json
   ```

3. Import the signed JSON bundle into Backtest Lab.
4. Run the bounded replay. Invalid, incomplete, duplicate, or unsafe material
   is rejected with HTTP 422.
5. Review equity/drawdown, capacity rejects, cost attribution, skipped signals,
   and event-level reasons.
6. Export the hash-bound JSON result for offline review.

A successful run still cannot set `strategy_equivalent=true`. That decision
requires the independent STRICT, replay-equivalence, walk-forward, purged,
calibration, multi-regime holdout, and uncertainty gates defined by the program
requirements.
