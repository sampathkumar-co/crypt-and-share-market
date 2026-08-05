from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Mapping, Sequence


@dataclass(frozen=True)
class BaselineSignal:
    weights: dict[str, float]
    reason: str


def cash_baseline() -> BaselineSignal:
    return BaselineSignal({}, "cash")


def buy_and_hold_baseline(assets: Sequence[str], maximum_total_exposure: float = 0.10) -> BaselineSignal:
    if not assets:
        return cash_baseline()
    weight = maximum_total_exposure / len(assets)
    return BaselineSignal({asset: weight for asset in assets}, "equal_weight_buy_and_hold")


def cross_sectional_trend(
    histories: Mapping[str, Sequence[float]],
    *,
    lookbacks: tuple[int, ...] = (7, 30, 90),
    maximum_total_exposure: float = 0.10,
    maximum_asset_exposure: float = 0.05,
) -> BaselineSignal:
    scores: list[tuple[str, float]] = []
    required = max(lookbacks) + 1
    for asset, prices in histories.items():
        if len(prices) < required or any(price <= 0 for price in prices):
            continue
        returns = [prices[-1] / prices[-1 - period] - 1.0 for period in lookbacks]
        daily = [prices[index] / prices[index - 1] - 1.0 for index in range(len(prices) - 29, len(prices))]
        volatility = pstdev(daily) if len(daily) > 1 else 0.0
        score = mean(returns) / max(volatility, 1e-6)
        if min(returns) > 0:
            scores.append((asset, score))
    scores.sort(key=lambda item: item[1], reverse=True)
    selected = scores[:2]
    if not selected:
        return BaselineSignal({}, "trend_filters_force_cash")
    raw = maximum_total_exposure / len(selected)
    weight = min(raw, maximum_asset_exposure)
    return BaselineSignal({asset: weight for asset, _ in selected}, "cross_sectional_multi_horizon_trend")
