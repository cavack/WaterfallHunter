"""Pure scheduling helpers for the live hunter loop."""

from __future__ import annotations

from typing import Any, Mapping


DEFAULT_EVALUATION_CONCURRENCY = 12

_STATE_PRIORITY = {
    "TRIGGERED": 0,
    "ARMED": 1,
    "PRE-TRIGGER": 2,
    "FUEL-RICH": 3,
    "WATCH": 4,
}


_STATE_EVALUATION_INTERVAL_SECONDS = {
    "TRIGGERED": 15.0,
    "ARMED": 15.0,
    "PRE-TRIGGER": 30.0,
    "FUEL-RICH": 90.0,
    "WATCH": 150.0,
}


def evaluation_interval_seconds(state: object) -> float:
    """Return the runtime-only start-to-start evaluation budget for a state."""
    normalized = str(state or "WATCH").upper()
    return _STATE_EVALUATION_INTERVAL_SECONDS.get(normalized, 150.0)


class HunterDeadlineSchedule:
    """Process-local per-symbol deadlines with promotion-only acceleration."""

    def __init__(self) -> None:
        self.next_due_at: dict[str, float] = {}
        self.last_started_at: dict[str, float] = {}

    def sync(self, candidates: Mapping[str, dict[str, Any]], *, now: float) -> None:
        active = set(candidates)
        for mapping in (self.next_due_at, self.last_started_at):
            for symbol in tuple(mapping):
                if symbol not in active:
                    mapping.pop(symbol, None)

        current_now = float(now)
        for symbol, data in candidates.items():
            if symbol not in self.next_due_at:
                self.next_due_at[symbol] = current_now
                continue
            last_started = self.last_started_at.get(symbol)
            if last_started is None:
                continue
            desired_due = last_started + evaluation_interval_seconds(data.get("status"))
            self.next_due_at[symbol] = min(self.next_due_at[symbol], desired_due)

    def mark_started(self, symbol: str, state: object, *, now: float) -> None:
        started = float(now)
        self.last_started_at[symbol] = started
        self.next_due_at[symbol] = started + evaluation_interval_seconds(state)

    def due_candidates(
        self,
        candidates: Mapping[str, dict[str, Any]],
        live_candidates: Mapping[str, dict[str, Any]] | None,
        *,
        now: float,
        in_flight: set[str],
        limit: int,
    ) -> list[tuple[str, dict[str, Any]]]:
        if limit <= 0:
            return []
        live = live_candidates or {}
        current_now = float(now)
        due: list[tuple[str, dict[str, Any]]] = []
        for symbol, data in candidates.items():
            if symbol in in_flight:
                continue
            due_at = self.next_due_at.get(symbol)
            if due_at is None or due_at > current_now:
                continue
            due.append((symbol, data))

        def sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, float, float, str]:
            symbol, data = item
            state = str(data.get("status") or "WATCH").upper()
            live_data = live.get(symbol) if isinstance(live.get(symbol), dict) else {}
            observed_at = live_data.get("analysis_observed_at")
            if isinstance(observed_at, bool) or not isinstance(observed_at, (int, float)):
                observed_at = 0.0
            return (
                _STATE_PRIORITY.get(state, 5),
                float(self.next_due_at.get(symbol, 0.0)),
                float(observed_at),
                symbol,
            )

        due.sort(key=sort_key)
        selected = due[: int(limit)]

        watch_in_flight = any(
            str(candidates.get(symbol, {}).get("status") or "WATCH").upper() == "WATCH"
            for symbol in in_flight
        )
        if not watch_in_flight:
            watch_due = next(
                (
                    item
                    for item in due
                    if str(item[1].get("status") or "WATCH").upper() == "WATCH"
                ),
                None,
            )
            if watch_due is not None and watch_due not in selected:
                selected[-1] = watch_due

        return selected

    def seconds_until_next_due(
        self,
        candidates: Mapping[str, dict[str, Any]],
        *,
        now: float,
        in_flight: set[str],
    ) -> float | None:
        due_times = [
            self.next_due_at[symbol]
            for symbol in candidates
            if symbol not in in_flight and symbol in self.next_due_at
        ]
        if not due_times:
            return None
        return max(0.0, min(due_times) - float(now))


def remaining_cycle_delay(
    cycle_started_at: float,
    now: float,
    target_interval_seconds: float,
) -> float:
    """Return delay needed to maintain a start-to-start target period."""
    elapsed = max(0.0, float(now) - float(cycle_started_at))
    return max(0.0, float(target_interval_seconds) - elapsed)


def ordered_hunter_candidates(
    candidates: Mapping[str, dict[str, Any]],
    live_candidates: Mapping[str, dict[str, Any]] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Prioritize near-trigger states, then the stalest observation."""
    live = live_candidates or {}

    def sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, float, str]:
        symbol, data = item
        state = str(data.get("status") or "WATCH").upper()
        live_data = live.get(symbol) if isinstance(live.get(symbol), dict) else {}
        observed_at = live_data.get("analysis_observed_at")
        if isinstance(observed_at, bool) or not isinstance(observed_at, (int, float)):
            observed_at = 0.0
        return (_STATE_PRIORITY.get(state, 5), float(observed_at), symbol)

    return sorted(candidates.items(), key=sort_key)
