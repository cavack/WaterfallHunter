import waterfallhunter.core.hunter_schedule as hunter_schedule

from waterfallhunter.core.hunter_schedule import (
    DEFAULT_EVALUATION_CONCURRENCY,
    ordered_hunter_candidates,
    remaining_cycle_delay,
)


def test_hunter_cycle_delay_targets_start_to_start_period() -> None:
    assert remaining_cycle_delay(100.0, 150.0, 60.0) == 10.0
    assert remaining_cycle_delay(100.0, 175.0, 60.0) == 0.0


def test_hunter_concurrency_has_capacity_for_180_second_freshness_budget() -> None:
    assert DEFAULT_EVALUATION_CONCURRENCY >= 10


def test_hunter_orders_near_trigger_and_oldest_analysis_first() -> None:
    candidates = {
        "WATCH_NEW": {"status": "WATCH"},
        "FUEL_OLD": {"status": "FUEL-RICH"},
        "PRE_NEW": {"status": "PRE-TRIGGER"},
        "PRE_OLD": {"status": "PRE-TRIGGER"},
        "ARMED": {"status": "ARMED"},
        "TRIGGERED": {"status": "TRIGGERED"},
    }
    live = {
        "WATCH_NEW": {"analysis_observed_at": 190.0},
        "FUEL_OLD": {"analysis_observed_at": 50.0},
        "PRE_NEW": {"analysis_observed_at": 180.0},
        "PRE_OLD": {"analysis_observed_at": 40.0},
        "ARMED": {"analysis_observed_at": 170.0},
        "TRIGGERED": {"analysis_observed_at": 160.0},
    }

    ordered = ordered_hunter_candidates(candidates, live)

    assert [symbol for symbol, _ in ordered] == [
        "TRIGGERED",
        "ARMED",
        "PRE_OLD",
        "PRE_NEW",
        "FUEL_OLD",
        "WATCH_NEW",
    ]


def test_deadline_schedule_new_candidates_are_due_and_state_priority_wins() -> None:
    factory = getattr(hunter_schedule, "HunterDeadlineSchedule", None)
    assert factory is not None
    schedule = factory()
    candidates = {
        "WATCH": {"status": "WATCH"},
        "PRE": {"status": "PRE-TRIGGER"},
    }
    schedule.sync(candidates, now=100.0)

    due = schedule.due_candidates(
        candidates, {}, now=100.0, in_flight=set(), limit=2
    )

    assert [symbol for symbol, _ in due] == ["PRE", "WATCH"]


def test_deadline_schedule_start_to_start_intervals_and_promotion_expedite() -> None:
    factory = getattr(hunter_schedule, "HunterDeadlineSchedule", None)
    assert factory is not None
    schedule = factory()
    candidates = {"X": {"status": "WATCH"}}
    schedule.sync(candidates, now=100.0)
    schedule.mark_started("X", "WATCH", now=100.0)
    assert schedule.seconds_until_next_due(candidates, now=100.0, in_flight=set()) == 150.0

    candidates["X"]["status"] = "PRE-TRIGGER"
    schedule.sync(candidates, now=110.0)
    assert schedule.seconds_until_next_due(candidates, now=110.0, in_flight=set()) == 20.0


def test_deadline_schedule_never_returns_inflight_and_prunes_removed() -> None:
    factory = getattr(hunter_schedule, "HunterDeadlineSchedule", None)
    assert factory is not None
    schedule = factory()
    candidates = {
        "KEEP": {"status": "FUEL-RICH"},
        "DROP": {"status": "WATCH"},
    }
    schedule.sync(candidates, now=100.0)
    assert schedule.due_candidates(
        candidates, {}, now=100.0, in_flight={"KEEP"}, limit=5
    ) == [("DROP", candidates["DROP"])]

    schedule.sync({"KEEP": candidates["KEEP"]}, now=101.0)
    assert "DROP" not in schedule.next_due_at
    assert schedule.due_candidates(
        {"KEEP": candidates["KEEP"]}, {}, now=101.0, in_flight={"KEEP"}, limit=5
    ) == []


def test_deadline_schedule_demotion_does_not_postpone_already_due_work() -> None:
    factory = getattr(hunter_schedule, "HunterDeadlineSchedule", None)
    assert factory is not None
    schedule = factory()
    candidates = {"X": {"status": "PRE-TRIGGER"}}
    schedule.sync(candidates, now=100.0)
    schedule.mark_started("X", "PRE-TRIGGER", now=100.0)

    candidates["X"]["status"] = "WATCH"
    schedule.sync(candidates, now=135.0)

    assert schedule.seconds_until_next_due(candidates, now=135.0, in_flight=set()) == 0.0


def test_state_evaluation_intervals_are_bounded_runtime_budgets() -> None:
    interval = getattr(hunter_schedule, "evaluation_interval_seconds", None)
    assert interval is not None
    assert interval("TRIGGERED") == 15.0
    assert interval("ARMED") == 15.0
    assert interval("PRE-TRIGGER") == 30.0
    assert interval("FUEL-RICH") == 90.0
    assert interval("WATCH") == 150.0
    assert interval("UNKNOWN") == 150.0


def test_deadline_schedule_reserves_one_slot_for_due_watch_work() -> None:
    factory = getattr(hunter_schedule, "HunterDeadlineSchedule", None)
    assert factory is not None
    schedule = factory()
    candidates = {
        **{f"FUEL_{i}": {"status": "FUEL-RICH"} for i in range(12)},
        "WATCH_DUE": {"status": "WATCH"},
    }
    schedule.sync(candidates, now=100.0)
    in_flight = {f"FUEL_{i}" for i in range(11)}

    due = schedule.due_candidates(
        candidates, {}, now=100.0, in_flight=in_flight, limit=1
    )

    assert [symbol for symbol, _ in due] == ["WATCH_DUE"]


def test_deadline_schedule_does_not_reserve_second_watch_slot() -> None:
    factory = getattr(hunter_schedule, "HunterDeadlineSchedule", None)
    assert factory is not None
    schedule = factory()
    candidates = {
        "FUEL_DUE": {"status": "FUEL-RICH"},
        "WATCH_RUNNING": {"status": "WATCH"},
        "WATCH_DUE": {"status": "WATCH"},
    }
    schedule.sync(candidates, now=100.0)

    due = schedule.due_candidates(
        candidates, {}, now=100.0, in_flight={"WATCH_RUNNING"}, limit=1
    )

    assert [symbol for symbol, _ in due] == ["FUEL_DUE"]
