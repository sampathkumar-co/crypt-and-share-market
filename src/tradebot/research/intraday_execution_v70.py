from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from tradebot.research.intraday_alpha_lab_v70 import (
    ASSETS,
    STANDARD_ROUND_TRIP_BPS,
    STRESS_ROUND_TRIP_BPS,
    validate_target,
)
from tradebot.research.intraday_signal_engine_v70 import SignalDecision


@dataclass(frozen=True)
class ExecutionPrices:
    next_five_minute_open: Mapping[str, float]
    following_hour_open: Mapping[str, float]
    delayed_five_minute_open: Mapping[str, float]
    primary_source: str
    replication_source: str
    sources_consistent: bool
    next_bar_after_signal: bool


@dataclass(frozen=True)
class PaperEpisode:
    family: str
    target: Mapping[str, float]
    action_count: int
    standard_net_return: float
    stress_net_return: float
    delayed_stress_net_return: float
    reason: str
    paper_only: bool = True
    authorizes_trading: bool = False


def _valid_prices(values: Mapping[str, float]) -> bool:
    return set(values) == set(ASSETS) and all(float(values[a]) > 0.0 for a in ASSETS)


def _portfolio_return(target: Mapping[str, float], entry: Mapping[str, float], exit_: Mapping[str, float]) -> float:
    return sum(float(weight) * (float(exit_[asset]) / float(entry[asset]) - 1.0) for asset, weight in target.items())


def _round_trip_cost(target: Mapping[str, float], bps: float) -> float:
    return sum(float(weight) for weight in target.values()) * float(bps) / 10_000.0


def evaluate_paper_episode(decision: SignalDecision, prices: ExecutionPrices) -> PaperEpisode:
    validate_target(decision.target)
    if decision.authorizes_trading or not decision.paper_only:
        raise ValueError("v7 decisions must remain paper-only and non-authorizing")
    if not prices.next_bar_after_signal:
        return PaperEpisode(decision.family.value, {}, 0, 0.0, 0.0, 0.0, "execution clock invalid; forced cash")
    if not prices.sources_consistent or prices.primary_source == prices.replication_source:
        return PaperEpisode(decision.family.value, {}, 0, 0.0, 0.0, 0.0, "independent sources missing or conflicting; forced cash")
    if not all(
        _valid_prices(values)
        for values in (prices.next_five_minute_open, prices.following_hour_open, prices.delayed_five_minute_open)
    ):
        return PaperEpisode(decision.family.value, {}, 0, 0.0, 0.0, 0.0, "missing or invalid prices; forced cash")
    if not decision.target:
        return PaperEpisode(decision.family.value, {}, 0, 0.0, 0.0, 0.0, decision.reason)

    gross = _portfolio_return(decision.target, prices.next_five_minute_open, prices.following_hour_open)
    delayed_gross = _portfolio_return(decision.target, prices.delayed_five_minute_open, prices.following_hour_open)
    standard = gross - _round_trip_cost(decision.target, STANDARD_ROUND_TRIP_BPS)
    stress = gross - _round_trip_cost(decision.target, STRESS_ROUND_TRIP_BPS)
    # Frozen adverse test adds 5 bps slippage per side to the 40 bps stress model.
    delayed_stress = delayed_gross - _round_trip_cost(decision.target, STRESS_ROUND_TRIP_BPS + 10.0)
    return PaperEpisode(
        family=decision.family.value,
        target=dict(decision.target),
        action_count=1,
        standard_net_return=standard,
        stress_net_return=stress,
        delayed_stress_net_return=delayed_stress,
        reason="next-5-minute-open paper episode evaluated",
    )
