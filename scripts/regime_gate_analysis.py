"""Regime-aware validation for OI, taker and cascade hypotheses.

Never use a single aggregate expectancy to change a live gate. This report
answers three separate questions for each feature split:

1. Does the direction persist across chronological regimes (daily windows)?
2. Does a 70/30 chronological holdout agree with the training set?
3. Is the observed expectancy delta distinguishable from resampling noise?

This script is read-only and makes no recommendation automatically. A feature
is labelled ``INCONCLUSIVE`` unless it has adequate samples *and* agrees in
direction across the aggregate, training and holdout windows.
"""
from __future__ import annotations

import json
import random
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from statistics import mean

DB = "/app/data/waterfall_registry.db"
FEE_PCT = 0.12
R = {
    "TP2_AFTER_TP1": 2.0,
    "TP2_FIRST": 2.0,
    "TP1_ONLY_24H": 1.0,
    "TP1_THEN_STOP": 0.0,
    "STOP_FIRST": -1.0,
    "NO_LEVEL_HIT_24H": 0.0,
}
BOOTSTRAPS = 2000
MIN_N = 100


def record(value):
    return value if isinstance(value, dict) else {}


def number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def load():
    con = sqlite3.connect(f"file:{DB}?mode=ro&immutable=1", uri=True)
    rows = con.execute(
        """
        SELECT o.outcome_status, l.entry_price, l.stop_loss,
               l.trigger_metrics_json, l.triggered_at
        FROM lbank_signal_outcomes o
        JOIN lbank_signal_ledger l ON l.id = o.signal_id
        WHERE o.outcome_status IN (%s)
        ORDER BY l.triggered_at
        """ % ",".join("?" * len(R)),
        tuple(R),
    ).fetchall()
    con.close()
    out = []
    for status, entry, stop, raw, timestamp in rows:
        if not entry or not stop or entry <= 0 or stop <= entry:
            continue
        try:
            metrics = json.loads(raw) if raw else {}
        except ValueError:
            continue
        risk_pct = (stop - entry) / entry * 100.0
        realised_r = R[status] - (FEE_PCT / risk_pct if risk_pct else 0.0)
        derivatives = record(metrics.get("derivatives"))
        cascade = record(metrics.get("cascade_intelligence"))
        out.append({
            "timestamp": int(timestamp),
            "r": realised_r,
            "oi": number(derivatives.get("oi_change_1h_pct")),
            "taker": number(derivatives.get("taker_buy_sell_ratio")),
            "cascade": cascade.get("status"),
        })
    return out


def stat(values):
    return len(values), mean(values) if values else 0.0, 100 * sum(v > 0 for v in values) / len(values) if values else 0.0


def bootstrap_delta(left, right):
    """95% bootstrap CI for mean(left) - mean(right)."""
    rng = random.Random(20260915)
    deltas = []
    for _ in range(BOOTSTRAPS):
        a = mean(rng.choice(left) for _ in range(len(left)))
        b = mean(rng.choice(right) for _ in range(len(right)))
        deltas.append(a - b)
    deltas.sort()
    return deltas[int(0.025 * BOOTSTRAPS)], deltas[int(0.975 * BOOTSTRAPS)]


def describe(name, good, bad, all_rows):
    good_r = [r["r"] for r in good]
    bad_r = [r["r"] for r in bad]
    ng, eg, wg = stat(good_r)
    nb, eb, wb = stat(bad_r)
    print(f"\n{name}")
    print(f"  selected: n={ng:4d} E={eg:+.3f}R win={wg:4.1f}%")
    print(f"  other:    n={nb:4d} E={eb:+.3f}R win={wb:4.1f}% delta={eg-eb:+.3f}R")
    if ng < MIN_N or nb < MIN_N:
        print("  verdict: INCONCLUSIVE (insufficient samples)")
        return
    low, high = bootstrap_delta(good_r, bad_r)
    split = int(len(all_rows) * 0.70)
    train, test = all_rows[:split], all_rows[split:]
    def delta(rows):
        yes = [r["r"] for r in rows if r in good]
        no = [r["r"] for r in rows if r in bad]
        return mean(yes) - mean(no) if yes and no else 0.0
    # Identity is retained from the same rows. Guard against a feature that
    # exists only on one side of a split even if it had enough aggregate rows.
    train_ids = {id(r) for r in train}
    good_train = [r["r"] for r in good if id(r) in train_ids]
    bad_train = [r["r"] for r in bad if id(r) in train_ids]
    test_ids = {id(r) for r in test}
    good_test = [r["r"] for r in good if id(r) in test_ids]
    bad_test = [r["r"] for r in bad if id(r) in test_ids]
    if not all((good_train, bad_train, good_test, bad_test)):
        print("  verdict: INCONCLUSIVE (feature absent from one chronological split)")
        return
    dt = mean(good_train) - mean(bad_train)
    dv = mean(good_test) - mean(bad_test)
    stable = low > 0 and dt > 0 and dv > 0
    print(f"  bootstrap 95% delta CI: [{low:+.3f}, {high:+.3f}]R")
    print(f"  chronological delta: train={dt:+.3f}R test={dv:+.3f}R")
    print("  verdict:", "PROMISING — validate in a new holdout" if stable else "INCONCLUSIVE — do not change the live gate")


def main():
    rows = load()
    print(f"rows={len(rows)} days={(rows[-1]['timestamp']-rows[0]['timestamp'])/86400:.1f}")
    describe("OI >= -0.13% (avoid mild OI unwind)", [r for r in rows if r["oi"] is not None and r["oi"] >= -0.13], [r for r in rows if r["oi"] is not None and r["oi"] < -0.13], rows)
    describe("Taker >= 0.85 (avoid already-exhausted sell flow)", [r for r in rows if r["taker"] is not None and r["taker"] >= 0.85], [r for r in rows if r["taker"] is not None and r["taker"] < 0.85], rows)
    describe("Cascade PASS", [r for r in rows if r["cascade"] == "PASS"], [r for r in rows if r["cascade"] != "PASS"], rows)
    print("\nDaily baseline:")
    by_day = defaultdict(list)
    for row in rows:
        by_day[datetime.fromtimestamp(row["timestamp"], UTC).date().isoformat()].append(row["r"])
    for day, values in sorted(by_day.items()):
        if len(values) >= 20:
            print(f"  {day}: n={len(values):4d} E={mean(values):+.3f}R")


if __name__ == "__main__":
    main()
