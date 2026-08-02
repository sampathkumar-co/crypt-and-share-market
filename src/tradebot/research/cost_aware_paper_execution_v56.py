from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

BPS = 10_000.0


class ExecutionMode(str, Enum):
    GUARANTEED_CASH = "GUARANTEED_CASH"
    LOSS_AVERSE_PAPER = "LOSS_AVERSE_PAPER"


@dataclass(frozen=True)
class CostPolicy:
    one_way_fee: float = 0.001
    slippage_bps_per_side: float = 2.5
    profit_buffer_bps: float = 10.0
    max_spread_bps: float = 5.0
    uncertainty_z: float = 1.28
    max_allocation: float = 0.25
    take_profit_net_bps: float = 25.0
    profit_lock_activation_bps: float = 15.0
    profit_lock_floor_bps: float = 5.0
    hard_stop_net_bps: float = 25.0
    max_hold_seconds: int = 3600


@dataclass(frozen=True)
class MarketSignal:
    asset: str
    score: float
    ret5: float
    ret15: float
    ret60: float
    last_close: float
    vwap20: float
    volatility60: float
    spread_bps: float


@dataclass(frozen=True)
class EntryAssessment:
    asset: str
    eligible: bool
    required_edge_bps: float
    confirmed_trend_bps: float
    uncertainty_bps: float
    lower_bound_edge_bps: float
    margin_bps: float
    reason: str
    score: float


@dataclass(frozen=True)
class EntryDecision:
    selected_asset: str | None
    allocation: float
    mode: ExecutionMode
    assessments: tuple[EntryAssessment, ...]
    reason: str


@dataclass(frozen=True)
class PositionState:
    asset: str
    entry_ask: float
    peak_bid: float


@dataclass(frozen=True)
class ExitDecision:
    action: str
    current_net_bps: float
    peak_net_bps: float
    reason: str


def fee_break_even_bps(one_way_fee: float) -> float:
    if not 0.0 <= one_way_fee < 1.0:
        raise ValueError("one_way_fee must be in [0, 1)")
    return (((1.0 + one_way_fee) / (1.0 - one_way_fee)) - 1.0) * BPS


def required_edge_bps(spread_bps: float, policy: CostPolicy) -> float:
    if spread_bps < 0.0:
        raise ValueError("spread_bps must be non-negative")
    return (
        fee_break_even_bps(policy.one_way_fee)
        + spread_bps
        + 2.0 * policy.slippage_bps_per_side
        + policy.profit_buffer_bps
    )


def conservative_edge(signal: MarketSignal, policy: CostPolicy) -> tuple[float, float, float]:
    confirmed = min(signal.ret15, signal.ret60) * BPS
    uncertainty = (
        policy.uncertainty_z
        * signal.volatility60
        * math.sqrt(60.0)
        * BPS
    )
    lower_bound = confirmed - uncertainty
    return confirmed, uncertainty, lower_bound


def assess_entry(signal: MarketSignal, policy: CostPolicy) -> EntryAssessment:
    required = required_edge_bps(signal.spread_bps, policy)
    confirmed, uncertainty, lower_bound = conservative_edge(signal, policy)
    conditions = {
        "5m momentum not positive": signal.ret5 <= 0.0,
        "15m momentum not positive": signal.ret15 <= 0.0,
        "60m momentum not positive": signal.ret60 <= 0.0,
        "price not above VWAP": signal.last_close <= signal.vwap20,
        "spread above cap": signal.spread_bps > policy.max_spread_bps,
        "lower-bound edge does not cover costs": lower_bound <= required,
    }
    failures = [name for name, failed in conditions.items() if failed]
    eligible = not failures
    return EntryAssessment(
        asset=signal.asset,
        eligible=eligible,
        required_edge_bps=required,
        confirmed_trend_bps=confirmed,
        uncertainty_bps=uncertainty,
        lower_bound_edge_bps=lower_bound,
        margin_bps=lower_bound - required,
        reason="eligible" if eligible else "; ".join(failures),
        score=signal.score,
    )


def choose_entry(
    signals: Iterable[MarketSignal],
    policy: CostPolicy | None = None,
    mode: ExecutionMode = ExecutionMode.LOSS_AVERSE_PAPER,
) -> EntryDecision:
    active_policy = policy or CostPolicy()
    assessments = tuple(
        sorted(
            (assess_entry(signal, active_policy) for signal in signals),
            key=lambda value: (-value.margin_bps, -value.score, value.asset),
        )
    )
    if mode is ExecutionMode.GUARANTEED_CASH:
        return EntryDecision(
            selected_asset=None,
            allocation=0.0,
            mode=mode,
            assessments=assessments,
            reason="zero trading loss requires no market exposure",
        )
    eligible = [value for value in assessments if value.eligible]
    if not eligible:
        return EntryDecision(
            selected_asset=None,
            allocation=0.0,
            mode=mode,
            assessments=assessments,
            reason="no asset clears costs and uncertainty; remain cash",
        )
    selected = eligible[0]
    return EntryDecision(
        selected_asset=selected.asset,
        allocation=active_policy.max_allocation,
        mode=mode,
        assessments=assessments,
        reason="best lower-bound edge after all execution costs",
    )


def net_return(entry_ask: float, exit_bid: float, policy: CostPolicy) -> float:
    if min(entry_ask, exit_bid) <= 0.0:
        raise ValueError("prices must be positive")
    return (
        (exit_bid * (1.0 - policy.one_way_fee))
        / (entry_ask * (1.0 + policy.one_way_fee))
        - 1.0
    )


def break_even_exit_bid(entry_ask: float, policy: CostPolicy) -> float:
    if entry_ask <= 0.0:
        raise ValueError("entry_ask must be positive")
    return entry_ask * (1.0 + policy.one_way_fee) / (1.0 - policy.one_way_fee)


def evaluate_exit(
    position: PositionState,
    current_bid: float,
    elapsed_seconds: int,
    policy: CostPolicy | None = None,
    *,
    momentum_reversal: bool = False,
) -> ExitDecision:
    active_policy = policy or CostPolicy()
    current_bps = net_return(position.entry_ask, current_bid, active_policy) * BPS
    peak_bps = net_return(position.entry_ask, position.peak_bid, active_policy) * BPS
    if current_bps <= -active_policy.hard_stop_net_bps:
        return ExitDecision(
            "SELL_HARD_STOP",
            current_bps,
            peak_bps,
            "pre-frozen maximum loss reached",
        )
    if current_bps >= active_policy.take_profit_net_bps:
        return ExitDecision(
            "SELL_TAKE_PROFIT",
            current_bps,
            peak_bps,
            "net profit target reached",
        )
    if (
        peak_bps >= active_policy.profit_lock_activation_bps
        and current_bps <= active_policy.profit_lock_floor_bps
    ):
        return ExitDecision(
            "SELL_PROFIT_PROTECTION",
            current_bps,
            peak_bps,
            "activated profit floor was reached",
        )
    if momentum_reversal and current_bps > 0.0:
        return ExitDecision(
            "SELL_MOMENTUM_REVERSAL",
            current_bps,
            peak_bps,
            "momentum reversed while the position remained profitable",
        )
    if elapsed_seconds >= active_policy.max_hold_seconds:
        return ExitDecision(
            "SELL_TIME_EXIT",
            current_bps,
            peak_bps,
            "maximum holding time reached",
        )
    return ExitDecision(
        "HOLD",
        current_bps,
        peak_bps,
        "no exit condition reached",
    )


__all__ = [
    "BPS",
    "CostPolicy",
    "EntryAssessment",
    "EntryDecision",
    "ExecutionMode",
    "ExitDecision",
    "MarketSignal",
    "PositionState",
    "assess_entry",
    "break_even_exit_bid",
    "choose_entry",
    "conservative_edge",
    "evaluate_exit",
    "fee_break_even_bps",
    "net_return",
    "required_edge_bps",
]
