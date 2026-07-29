from __future__ import annotations

import math
from statistics import fmean, pstdev


def max_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0] if equity_curve else 0.0
    worst = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak:
            worst = min(worst, (value - peak) / peak)
    return abs(worst)


def win_rate(pnls: list[float]) -> float:
    return sum(1 for pnl in pnls if pnl > 0) / len(pnls) if pnls else 0.0


def period_returns(equity_curve: list[float]) -> list[float]:
    return [
        (current - previous) / previous
        for previous, current in zip(equity_curve, equity_curve[1:])
        if previous > 0
    ]


def sharpe_ratio(returns: list[float], annualization: int) -> float:
    if len(returns) < 2:
        return 0.0
    volatility = pstdev(returns)
    return fmean(returns) / volatility * math.sqrt(annualization) if volatility > 0 else 0.0


def sortino_ratio(returns: list[float], annualization: int) -> float:
    if len(returns) < 2:
        return 0.0
    downside = [min(value, 0.0) for value in returns]
    downside_deviation = math.sqrt(fmean([value * value for value in downside]))
    return fmean(returns) / downside_deviation * math.sqrt(annualization) if downside_deviation > 0 else 0.0


def profit_factor(pnls: list[float]) -> float:
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss = abs(sum(value for value in pnls if value < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def expectancy(pnls: list[float]) -> float:
    return fmean(pnls) if pnls else 0.0
