#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from scripts.backtest_metrics import calculate_slippage_profile


def capture(url: str, *, venue: str | None, executable_notional: float,
            maximum_age_seconds: float, minimum_samples: int,
            minimum_quote_volume_usdt: float) -> dict:
    request = Request(url, headers={"User-Agent": "WaterfallHunter-Research/1.0"})
    with urlopen(request, timeout=20) as response:
        payload = json.load(response)
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    profile = calculate_slippage_profile(
        candidates,
        now=time.time(),
        executable_notional=executable_notional,
        venue=venue,
        max_age_seconds=maximum_age_seconds,
        minimum_samples=minimum_samples,
        minimum_quote_volume_usdt=minimum_quote_volume_usdt,
    )
    profile.update({
        "schema_version": "empirical_slippage_profile_v1",
        "captured_at": datetime.now(UTC).isoformat(),
        "source_url": url,
        "data_contract": "fresh live perpetual orderbook VWAP observations",
    })
    return profile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:3000/dashboard/api/candidates")
    parser.add_argument("--venue")
    parser.add_argument("--executable-notional", type=float, default=50.0)
    parser.add_argument("--maximum-age-seconds", type=float, default=90.0)
    parser.add_argument("--minimum-samples", type=int, default=20)
    parser.add_argument("--minimum-quote-volume-usdt", type=float, default=5_000_000.0)
    parser.add_argument("--output", default="research/slippage")
    args = parser.parse_args()
    profile = capture(
        args.url,
        venue=args.venue,
        executable_notional=args.executable_notional,
        maximum_age_seconds=args.maximum_age_seconds,
        minimum_samples=args.minimum_samples,
        minimum_quote_volume_usdt=args.minimum_quote_volume_usdt,
    )
    if not profile["available"]:
        raise SystemExit(json.dumps(profile, ensure_ascii=False))
    destination = Path(args.output)
    destination.mkdir(parents=True, exist_ok=True)
    venue = args.venue or "all"
    path = destination / f"slippage_{venue}_{int(time.time())}.json"
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2))
    print(json.dumps(profile, ensure_ascii=False))
    print(path)


if __name__ == "__main__":
    main()
