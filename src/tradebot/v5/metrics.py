from __future__ import annotations

from dataclasses import dataclass
from math import prod, sqrt
from statistics import mean, pstdev
from typing import Iterable, Mapping


@dataclass(frozen=True)
class PerformanceMetrics:
    compounded_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe: float
    maximum_drawdown: float
    win_rate: float
    profit_factor: float
    observations: int


def equity_curve(returns: Iterable[float]) -> list[float]:
    curve = [1.0]
    for value in returns:
        if value <= -1:
            raise ValueError("return cannot be less than or equal to -100%")
        curve.append(curve[-1] * (1.0 + value))
    return curve


def maximum_drawdown(curve: Iterable[float]) -> float:
    peak = 0.0
    worst = 0.0
    for value in curve:
        if value <= 0:
            raise ValueError("equity values must be positive")
        peak = max(peak, value)
        worst = max(worst, 1.0 - value / peak)
    return worst


def performance_metrics(returns: Iterable[float], periods_per_year: int = 365) -> PerformanceMetrics:
    values = list(returns)
    if not values:
        return PerformanceMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0)
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    curve = equity_curve(values)
    compounded = prod(1.0 + value for value in values) - 1.0
    years = len(values) / periods_per_year
    annualized = curve[-1] ** (1.0 / years) - 1.0 if years > 0 else 0.0
    volatility = pstdev(values) * sqrt(periods_per_year) if len(values) > 1 else 0.0
    sharpe = mean(values) * periods_per_year / volatility if volatility > 0 else 0.0
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    profit_factor = gains / losses if losses > 0 else (float("inf") if gains > 0 else 0.0)
    return PerformanceMetrics(
        compounded_return=compounded,
        annualized_return=annualized,
        annualized_volatility=volatility,
        sharpe=sharpe,
        maximum_drawdown=maximum_drawdown(curve),
        win_rate=sum(value > 0 for value in values) / len(values),
        profit_factor=profit_factor,
        observations=len(values),
    )


def contribution_concentration(contributions: Mapping[str, float]) -> float:
    positive = {key: value for key, value in contributions.items() if value > 0}
    total = sum(positive.values())
    if total <= 0:
        return 1.0
    return max(positive.values()) / total
