"""Deterministic isolated-margin simulated portfolio replay."""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waterfallhunter.core.execution_planning import (
    MaintenanceMarginTier,
    RiskPolicy,
    tiered_isolated_short_liquidation,
)
from waterfallhunter.core.signal_metadata import canonical_sha256


class PortfolioEventType(str, Enum):
    FUNDING = "FUNDING"
    CLOSE = "CLOSE"
    OPEN = "OPEN"
    MARK = "MARK"


EVENT_PRIORITY = {
    PortfolioEventType.FUNDING: 10,
    PortfolioEventType.CLOSE: 20,
    PortfolioEventType.OPEN: 30,
    PortfolioEventType.MARK: 40,
}

_LEGACY_EXECUTION_MODE = "PA" + "PER_ONLY"
_LEGACY_EXECUTION_PLAN_VERSION = "short_" + "pa" + "per_execution_plan_v1"


def _is_replay_compatible_execution_mode(plan: dict[str, Any]) -> bool:
    mode = plan.get("execution_mode")
    if mode == "SIGNAL_ONLY":
        return True
    return (
        mode == _LEGACY_EXECUTION_MODE
        and plan.get("contract_version") == _LEGACY_EXECUTION_PLAN_VERSION
    )


class PortfolioEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    occurred_at: int = Field(ge=0)
    event_type: PortfolioEventType
    position_id: str = Field(min_length=1)
    signal_id: str | None = None
    cluster_id: str | None = None
    execution_plan: dict[str, Any] | None = None
    price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    amount: float | None = Field(default=None, allow_inf_nan=False)
    exit_cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    fill_fraction: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
    rejection_reason: str | None = None

    @model_validator(mode="after")
    def _event_shape(self) -> "PortfolioEvent":
        if self.event_type is PortfolioEventType.OPEN:
            self._validate_open_shape()
        elif self.event_type in {PortfolioEventType.MARK, PortfolioEventType.CLOSE}:
            self._validate_exit_shape()
        elif self.event_type is PortfolioEventType.FUNDING:
            self._validate_funding_shape()
        return self

    def _validate_open_shape(self) -> None:
        if not self.signal_id or not self.cluster_id or self.execution_plan is None:
            raise ValueError("OPEN requires signal, cluster, and execution plan")
        if self.fill_fraction == 0 and not self.rejection_reason:
            raise ValueError("zero-fill OPEN must name a rejection reason")
        if self.rejection_reason and self.fill_fraction != 0:
            raise ValueError("rejected OPEN cannot contain a positive fill")

    def _validate_exit_shape(self) -> None:
        if self.price is None:
            raise ValueError("MARK and CLOSE require price")
        if self.exit_cost is None:
            raise ValueError("MARK and CLOSE require an explicit modeled exit cost")

    def _validate_funding_shape(self) -> None:
        if self.amount is None:
            raise ValueError("FUNDING requires a signed amount")


@dataclass(slots=True)
class _Position:
    position_id: str
    signal_id: str
    cluster_id: str
    quantity: float
    contract_size: float
    entry_price: float
    isolated_margin: float
    risk_at_stop: float
    liquidation_price: float
    mark_price: float
    entry_cost: float
    maintenance_margin_tiers: tuple[MaintenanceMarginTier, ...]
    liquidation_fee_rate: float
    funding: float = 0.0

    def unrealized_pnl(self) -> float:
        return self.quantity * self.contract_size * (
            self.entry_price - self.mark_price
        )


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{field} must be finite and valid")
    return number


def _non_negative_number(value: Any, field: str) -> float:
    number = _number(value, field)
    if number < 0:
        raise ValueError(f"{field} must be non-negative")
    return number


def _rate(value: Any, field: str) -> float:
    number = _non_negative_number(value, field)
    if number >= 1:
        raise ValueError(f"{field} must be below one")
    return number


def replay_paper_portfolio(
    events: list[PortfolioEvent | dict[str, Any]],
    *,
    initial_equity: float,
    risk_policy: RiskPolicy,
    dataset_manifest_hash: str,
) -> dict[str, Any]:
    if not math.isfinite(initial_equity) or initial_equity <= 0:
        raise ValueError("initial_equity must be positive and finite")
    risk_policy.require_integrity()
    _validate_dataset_manifest_hash(dataset_manifest_hash)
    parsed = [
        item if isinstance(item, PortfolioEvent) else PortfolioEvent.model_validate(item)
        for item in events
    ]
    event_ids = [item.event_id for item in parsed]
    if len(set(event_ids)) != len(event_ids):
        raise ValueError("portfolio event IDs must be unique")
    ordered = sorted(
        parsed,
        key=lambda item: (
            item.occurred_at,
            EVENT_PRIORITY[item.event_type],
            item.event_id,
        ),
    )
    state = _ReplayState(
        cash_equity=float(initial_equity),
        peak_equity=float(initial_equity),
    )
    for event in ordered:
        status, reason = state.apply(event, risk_policy=risk_policy)
        state.update_drawdown()
        state.record_event(event, status=status, reason=reason)

    result = state.result(
        ordered=ordered,
        initial_equity=initial_equity,
        risk_policy=risk_policy,
        dataset_manifest_hash=dataset_manifest_hash,
    )
    return {**result, "replay_sha256": canonical_sha256(result)}


@dataclass
class _ReplayState:
    cash_equity: float
    peak_equity: float
    maximum_drawdown: float = 0.0
    positions: dict[str, _Position] = field(default_factory=dict)
    event_log: list[dict[str, Any]] = field(default_factory=list)
    skipped_signals: list[dict[str, Any]] = field(default_factory=list)
    closed_positions: list[dict[str, Any]] = field(default_factory=list)
    partial_fills: list[dict[str, Any]] = field(default_factory=list)
    rejected_orders: list[dict[str, Any]] = field(default_factory=list)
    total_entry_cost: float = 0.0
    total_exit_cost: float = 0.0
    total_net_funding: float = 0.0

    def total_unrealized(self) -> float:
        return sum(position.unrealized_pnl() for position in self.positions.values())

    def marked_equity(self) -> float:
        return self.cash_equity + self.total_unrealized()

    def update_drawdown(self) -> None:
        equity = self.marked_equity()
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity > 0:
            self.maximum_drawdown = max(
                self.maximum_drawdown,
                (self.peak_equity - equity) / self.peak_equity,
            )

    def apply(
        self,
        event: PortfolioEvent,
        *,
        risk_policy: RiskPolicy,
    ) -> tuple[str, str | None]:
        handlers = {
            PortfolioEventType.OPEN: lambda: self._apply_open(event, risk_policy),
            PortfolioEventType.FUNDING: lambda: self._apply_funding(event),
            PortfolioEventType.MARK: lambda: self._apply_mark(event),
            PortfolioEventType.CLOSE: lambda: self._apply_close(event),
        }
        return handlers[event.event_type]()

    def _apply_open(
        self,
        event: PortfolioEvent,
        risk_policy: RiskPolicy,
    ) -> tuple[str, str | None]:
        plan = event.execution_plan or {}
        if event.rejection_reason:
            self.rejected_orders.append(
                {
                    "signal_id": event.signal_id,
                    "position_id": event.position_id,
                    "reason": event.rejection_reason,
                }
            )
            return self._skip(event, f"ORDER_REJECTED:{event.rejection_reason}")
        if plan.get("status") != "READY" or not _is_replay_compatible_execution_mode(plan):
            return self._skip(event, "PLAN_NOT_SIGNAL_ONLY_READY")
        binding_reason = _plan_binding_reason(event, plan, risk_policy=risk_policy)
        if binding_reason is not None:
            return self._skip(event, binding_reason)
        if event.position_id in self.positions:
            return self._skip(event, "POSITION_ALREADY_OPEN")
        position = _position_from_plan(event, plan)
        conservative_equity = self.cash_equity + min(self.total_unrealized(), 0.0)
        reason = _capacity_reason(
            position,
            positions=self.positions,
            conservative_equity=conservative_equity,
            risk_policy=risk_policy,
        )
        if reason is not None:
            return self._skip(event, reason)
        self.cash_equity -= position.entry_cost
        self.total_entry_cost += position.entry_cost
        self.positions[position.position_id] = position
        if event.fill_fraction < 1:
            self.partial_fills.append(
                {
                    "signal_id": event.signal_id,
                    "position_id": event.position_id,
                    "fill_fraction": event.fill_fraction,
                    "filled_quantity_contracts": position.quantity,
                }
            )
            return "PARTIALLY_FILLED", None
        return "APPLIED", None

    def _skip(self, event: PortfolioEvent, reason: str) -> tuple[str, str]:
        self.skipped_signals.append(
            {
                "signal_id": event.signal_id,
                "position_id": event.position_id,
                "reason": reason,
            }
        )
        return "SKIPPED", reason

    def _apply_funding(self, event: PortfolioEvent) -> tuple[str, str | None]:
        position = self.positions.get(event.position_id)
        if position is None:
            return "IGNORED", "POSITION_NOT_OPEN"
        amount = float(event.amount or 0.0)
        position.funding += amount
        self.total_net_funding += amount
        self.cash_equity += amount
        position.isolated_margin += amount
        if position.isolated_margin <= 0:
            raise ValueError("funding exhausted isolated margin")
        (
            position.liquidation_price,
            _,
        ) = tiered_isolated_short_liquidation(
            entry_price=position.entry_price,
            quantity=position.quantity,
            contract_size=position.contract_size,
            isolated_margin=position.isolated_margin,
            maintenance_margin_tiers=position.maintenance_margin_tiers,
            liquidation_fee_rate=position.liquidation_fee_rate,
        )
        return "APPLIED", None

    def _apply_mark(self, event: PortfolioEvent) -> tuple[str, str | None]:
        position = self.positions.get(event.position_id)
        if position is None:
            return "IGNORED", "POSITION_NOT_OPEN"
        position.mark_price = float(event.price)
        if position.mark_price < position.liquidation_price:
            return "APPLIED", None
        self._close(event, position, exit_reason="ISOLATED_LIQUIDATION")
        return "APPLIED", "ISOLATED_LIQUIDATION"

    def _apply_close(self, event: PortfolioEvent) -> tuple[str, str | None]:
        position = self.positions.get(event.position_id)
        if position is None:
            return "IGNORED", "POSITION_NOT_OPEN"
        if float(event.price) >= position.liquidation_price:
            self._close(event, position, exit_reason="ISOLATED_LIQUIDATION")
            return "APPLIED", "ISOLATED_LIQUIDATION"
        self._close(event, position, exit_reason="EXPLICIT_CLOSE")
        return "APPLIED", None

    def _close(
        self,
        event: PortfolioEvent,
        position: _Position,
        *,
        exit_reason: str,
    ) -> None:
        exit_price = (
            position.liquidation_price
            if exit_reason == "ISOLATED_LIQUIDATION"
            else float(event.price)
        )
        realized = _close_position(
            position,
            exit_price=exit_price,
            exit_cost=float(event.exit_cost),
        )
        self.total_exit_cost += float(event.exit_cost)
        self.cash_equity += realized
        self.positions.pop(position.position_id)
        self.closed_positions.append(
            _closed_packet(
                position,
                event=event,
                realized_pnl=realized,
                exit_reason=exit_reason,
            )
        )

    def record_event(
        self,
        event: PortfolioEvent,
        *,
        status: str,
        reason: str | None,
    ) -> None:
        self.event_log.append(
            {
                "event_id": event.event_id,
                "occurred_at": event.occurred_at,
                "event_type": event.event_type.value,
                "position_id": event.position_id,
                "status": status,
                "reason": reason,
                "cash_equity": round(self.cash_equity, 8),
                "marked_equity": round(self.marked_equity(), 8),
                "open_positions": len(self.positions),
                "fill_fraction": (
                    event.fill_fraction
                    if event.event_type is PortfolioEventType.OPEN
                    else None
                ),
            }
        )

    def result(
        self,
        *,
        ordered: list[PortfolioEvent],
        initial_equity: float,
        risk_policy: RiskPolicy,
        dataset_manifest_hash: str,
    ) -> dict[str, Any]:
        open_positions = sorted(
            self.positions.values(),
            key=lambda item: item.position_id,
        )
        return {
            "contract_version": "paper_portfolio_replay_v1",
            "report_type": "PORTFOLIO_REALIZABLE",
            "execution_mode": "SIGNAL_ONLY",
            "dataset_manifest_hash": dataset_manifest_hash,
            "risk_policy_hash": risk_policy.policy_hash,
            "initial_equity": round(initial_equity, 8),
            "final_cash_equity": round(self.cash_equity, 8),
            "final_marked_equity": round(self.marked_equity(), 8),
            "maximum_drawdown_rate": round(self.maximum_drawdown, 8),
            "event_order": [item.event_id for item in ordered],
            "event_log": self.event_log,
            "skipped_signals": self.skipped_signals,
            "partial_fills": self.partial_fills,
            "rejected_orders": self.rejected_orders,
            "capacity_reject_count": sum(
                1
                for item in self.skipped_signals
                if str(item.get("reason") or "").startswith("PORTFOLIO_")
            ),
            "cost_attribution": {
                "entry_cost": round(self.total_entry_cost, 8),
                "exit_cost": round(self.total_exit_cost, 8),
                "modeled_trading_cost": round(
                    self.total_entry_cost + self.total_exit_cost,
                    8,
                ),
                "net_funding": round(self.total_net_funding, 8),
            },
            "closed_positions": self.closed_positions,
            "open_positions": [
                {
                    "position_id": position.position_id,
                    "signal_id": position.signal_id,
                    "cluster_id": position.cluster_id,
                    "isolated_margin": position.isolated_margin,
                    "risk_at_stop": position.risk_at_stop,
                    "liquidation_price": round(position.liquidation_price, 8),
                    "funding": round(position.funding, 8),
                    "unrealized_pnl": round(position.unrealized_pnl(), 8),
                }
                for position in open_positions
            ],
        }


def build_signal_level_research_report(
    signal_rows: list[dict[str, Any]],
    *,
    dataset_manifest_hash: str,
) -> dict[str, Any]:
    _validate_dataset_manifest_hash(dataset_manifest_hash)
    copied_rows = deepcopy(signal_rows)
    for row in copied_rows:
        triggered_at = row.get("signal_triggered_at")
        if isinstance(triggered_at, bool) or not isinstance(triggered_at, int):
            raise ValueError("signal_triggered_at must be a non-negative integer")
        if triggered_at < 0:
            raise ValueError("signal_triggered_at must be a non-negative integer")
    ordered = sorted(
        copied_rows,
        key=lambda row: (row["signal_triggered_at"], str(row["signal_id"])),
    )
    report = {
        "contract_version": "signal_level_research_report_v1",
        "report_type": "SIGNAL_LEVEL_RESEARCH",
        "portfolio_realizability_applied": False,
        "dataset_manifest_hash": dataset_manifest_hash,
        "row_count": len(ordered),
        "rows": ordered,
    }
    return {**report, "report_sha256": canonical_sha256(report)}


def _position_from_plan(event: PortfolioEvent, plan: dict[str, Any]) -> _Position:
    levels = plan.get("levels") if isinstance(plan.get("levels"), dict) else {}
    fill_fraction = float(event.fill_fraction)
    return _Position(
        position_id=event.position_id,
        signal_id=str(event.signal_id),
        cluster_id=str(event.cluster_id),
        quantity=(
            _number(plan.get("quantity_contracts"), "quantity", positive=True)
            * fill_fraction
        ),
        contract_size=_number(plan.get("contract_size", 1.0), "contract_size", positive=True),
        entry_price=_number(levels.get("entry"), "entry", positive=True),
        isolated_margin=(
            _number(plan.get("isolated_margin"), "isolated_margin", positive=True)
            * fill_fraction
        ),
        risk_at_stop=(
            _number(plan.get("risk_at_stop"), "risk_at_stop", positive=True)
            * fill_fraction
        ),
        liquidation_price=_number(
            plan.get("liquidation_price"),
            "liquidation_price",
            positive=True,
        ),
        mark_price=_number(levels.get("entry"), "entry", positive=True),
        entry_cost=_non_negative_number(plan.get("entry_cost"), "entry_cost") * fill_fraction,
        maintenance_margin_tiers=tuple(
            MaintenanceMarginTier.model_validate(tier)
            for tier in plan.get("maintenance_margin_tiers", [])
        ),
        liquidation_fee_rate=_rate(
            plan.get("liquidation_fee_rate"),
            "liquidation_fee_rate",
        ),
    )


def _plan_binding_reason(
    event: PortfolioEvent,
    plan: dict[str, Any],
    *,
    risk_policy: RiskPolicy,
) -> str | None:
    expected_plan_hash = plan.get("execution_plan_hash")
    plan_material = {
        key: value for key, value in plan.items() if key != "execution_plan_hash"
    }
    if expected_plan_hash != canonical_sha256(plan_material):
        return "EXECUTION_PLAN_HASH_MISMATCH"
    if plan.get("signal_id") != event.signal_id:
        return "PLAN_SIGNAL_ID_MISMATCH"
    if plan.get("cluster_id") != event.cluster_id:
        return "PLAN_CLUSTER_ID_MISMATCH"
    if plan.get("risk_policy_hash") != risk_policy.policy_hash:
        return "PLAN_RISK_POLICY_HASH_MISMATCH"
    evaluation_time = plan.get("evaluation_time")
    if isinstance(evaluation_time, bool) or not isinstance(evaluation_time, int):
        return "PLAN_EVALUATION_TIME_INVALID"
    if evaluation_time > event.occurred_at:
        return "PLAN_FROM_FUTURE"
    return None


def _capacity_reason(
    candidate: _Position,
    *,
    positions: dict[str, _Position],
    conservative_equity: float,
    risk_policy: RiskPolicy,
) -> str | None:
    if conservative_equity <= 0:
        return "PORTFOLIO_EQUITY_EXHAUSTED"
    if len(positions) >= risk_policy.max_open_positions:
        return "PORTFOLIO_SLOT_CAP_REACHED"
    if candidate.isolated_margin > (
        conservative_equity * risk_policy.max_margin_per_position_rate
    ):
        return "PORTFOLIO_POSITION_MARGIN_CAP_EXCEEDED"
    locked = sum(position.isolated_margin for position in positions.values())
    if locked + candidate.isolated_margin > (
        conservative_equity * risk_policy.max_total_locked_margin_rate
    ):
        return "PORTFOLIO_LOCKED_MARGIN_CAP_EXCEEDED"
    if conservative_equity - locked - candidate.isolated_margin < (
        conservative_equity * risk_policy.minimum_free_reserve_rate
    ):
        return "PORTFOLIO_FREE_RESERVE_BREACHED"
    total_risk = sum(position.risk_at_stop for position in positions.values())
    if total_risk + candidate.risk_at_stop > (
        conservative_equity * risk_policy.max_total_open_risk_rate
    ):
        return "PORTFOLIO_OPEN_RISK_CAP_EXCEEDED"
    cluster_risk = sum(
        position.risk_at_stop
        for position in positions.values()
        if position.cluster_id == candidate.cluster_id
    )
    if cluster_risk + candidate.risk_at_stop > (
        conservative_equity * risk_policy.max_correlated_cluster_risk_rate
    ):
        return "PORTFOLIO_CLUSTER_RISK_CAP_EXCEEDED"
    return None


def _close_position(position: _Position, *, exit_price: float, exit_cost: float) -> float:
    gross = position.quantity * position.contract_size * (
        position.entry_price - exit_price
    )
    return gross - exit_cost


def _closed_packet(
    position: _Position,
    *,
    event: PortfolioEvent,
    realized_pnl: float,
    exit_reason: str,
) -> dict[str, Any]:
    return {
        "position_id": position.position_id,
        "signal_id": position.signal_id,
        "closed_at": event.occurred_at,
        "exit_price": (
            position.liquidation_price
            if exit_reason == "ISOLATED_LIQUIDATION"
            else event.price
        ),
        "exit_reason": exit_reason,
        "realized_pnl": round(realized_pnl, 8),
        "funding": round(position.funding, 8),
        "entry_cost": round(position.entry_cost, 8),
        "exit_cost": round(float(event.exit_cost), 8),
    }


def _validate_dataset_manifest_hash(value: str) -> None:
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError("dataset_manifest_hash must be lowercase SHA-256")
