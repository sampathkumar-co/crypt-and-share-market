from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import mean, pstdev
from typing import Mapping, Sequence

from tradebot.research.intraday_alpha_lab_v70 import (
    ASSETS,
    MAX_ASSET_EXPOSURE,
    CandidateFamily,
    lower_bound_trade_is_eligible,
    validate_target,
)


@dataclass(frozen=True)
class HourBar:
    close: float
    high: float
    low: float
    volume: float


@dataclass(frozen=True)
class SignalDecision:
    family: CandidateFamily
    target: Mapping[str, float]
    lower_bound_edge_bps: float
    reason: str
    paper_only: bool = True
    authorizes_trading: bool = False


def _returns(bars: Sequence[HourBar]) -> list[float]:
    return [bars[i].close / bars[i - 1].close - 1.0 for i in range(1, len(bars))]


def _require_history(history: Mapping[str, Sequence[HourBar]], minimum: int) -> None:
    if set(history) != set(ASSETS):
        raise ValueError("BTC and ETH histories are required")
    if any(len(history[a]) < minimum for a in ASSETS):
        raise ValueError(f"at least {minimum} completed hourly bars are required")
    for asset in ASSETS:
        if any(b.close <= 0 or b.high < b.low or b.volume < 0 for b in history[asset]):
            raise ValueError("invalid completed bar")


def _cash(family: CandidateFamily, edge: float, reason: str) -> SignalDecision:
    return SignalDecision(family, {}, edge, reason)


def _long(family: CandidateFamily, asset: str, edge: float, reason: str) -> SignalDecision:
    target = {asset: MAX_ASSET_EXPOSURE}
    validate_target(target)
    return SignalDecision(family, target, edge, reason)


def hourly_trend(history: Mapping[str, Sequence[HourBar]]) -> SignalDecision:
    _require_history(history, 25)
    scored: list[tuple[float, str]] = []
    for asset in ASSETS:
        closes = [b.close for b in history[asset]]
        rets = _returns(history[asset][-25:])
        momentum = closes[-1] / closes[-7] - 1.0
        vol = pstdev(rets) or 1e-12
        scored.append((10_000.0 * (momentum - 1.28 * vol / sqrt(6.0)), asset))
    edge, asset = max(scored)
    return _long(CandidateFamily.HOURLY_TREND, asset, edge, "positive cost-filtered trend") if lower_bound_trade_is_eligible(edge) else _cash(CandidateFamily.HOURLY_TREND, edge, "edge below frozen threshold")


def shock_reversal(history: Mapping[str, Sequence[HourBar]]) -> SignalDecision:
    _require_history(history, 26)
    candidates: list[tuple[float, str]] = []
    for asset in ASSETS:
        bars = history[asset]
        rets = _returns(bars[-26:])
        baseline = rets[:-2]
        sigma = pstdev(baseline) or 1e-12
        shock, stabilization = rets[-2], rets[-1]
        if shock < -2.5 * sigma and stabilization > 0 and abs(stabilization) < abs(shock) * 0.5:
            candidates.append((10_000.0 * (-shock * 0.35 - 1.28 * sigma), asset))
    if not candidates:
        return _cash(CandidateFamily.SHOCK_REVERSAL, 0.0, "no stabilized downside shock")
    edge, asset = max(candidates)
    return _long(CandidateFamily.SHOCK_REVERSAL, asset, edge, "stabilized post-shock reversal") if lower_bound_trade_is_eligible(edge) else _cash(CandidateFamily.SHOCK_REVERSAL, edge, "edge below frozen threshold")


def volatility_breakout(history: Mapping[str, Sequence[HourBar]]) -> SignalDecision:
    _require_history(history, 25)
    candidates: list[tuple[float, str]] = []
    for asset in ASSETS:
        bars = history[asset]
        prior = bars[-25:-1]
        breakout = bars[-1].close / max(b.high for b in prior) - 1.0
        ranges = [(b.high - b.low) / b.close for b in prior]
        volume_ratio = bars[-1].volume / (mean(b.volume for b in prior) or 1e-12)
        if breakout > 0 and volume_ratio >= 1.25:
            candidates.append((10_000.0 * (breakout - 1.28 * (pstdev(ranges) or 0.0)), asset))
    if not candidates:
        return _cash(CandidateFamily.VOLATILITY_BREAKOUT, 0.0, "no confirmed breakout")
    edge, asset = max(candidates)
    return _long(CandidateFamily.VOLATILITY_BREAKOUT, asset, edge, "range and volume confirmed breakout") if lower_bound_trade_is_eligible(edge) else _cash(CandidateFamily.VOLATILITY_BREAKOUT, edge, "edge below frozen threshold")


def relative_strength(history: Mapping[str, Sequence[HourBar]]) -> SignalDecision:
    _require_history(history, 25)
    momentum = {a: history[a][-1].close / history[a][-7].close - 1.0 for a in ASSETS}
    asset = max(momentum, key=momentum.get)
    other = "ETH" if asset == "BTC" else "BTC"
    absolute = momentum[asset]
    spread = absolute - momentum[other]
    sigma = pstdev(_returns(history[asset][-25:])) or 1e-12
    edge = 10_000.0 * (0.5 * spread + 0.5 * absolute - 1.28 * sigma / sqrt(6.0))
    if absolute <= 0:
        return _cash(CandidateFamily.RELATIVE_STRENGTH, edge, "absolute-trend veto")
    return _long(CandidateFamily.RELATIVE_STRENGTH, asset, edge, "relative strength with absolute trend") if lower_bound_trade_is_eligible(edge) else _cash(CandidateFamily.RELATIVE_STRENGTH, edge, "edge below frozen threshold")


def run_first_tournament(history: Mapping[str, Sequence[HourBar]]) -> tuple[SignalDecision, ...]:
    decisions = (
        hourly_trend(history),
        shock_reversal(history),
        volatility_breakout(history),
        relative_strength(history),
    )
    if {d.family for d in decisions} != set(CandidateFamily):
        raise AssertionError("exactly four preregistered families are required")
    return decisions
