from __future__ import annotations

from dataclasses import dataclass
from statistics import pstdev

from tradebot.models import Candle
from tradebot.strategies.base import Strategy
from tradebot.strategies.breakout import BreakoutStrategy
from tradebot.strategies.mean_reversion import MeanReversionStrategy
from tradebot.strategies.momentum import MomentumVolumeStrategy


@dataclass(frozen=True)
class RegimeFilterConfig:
    lookback: int = 30
    fast_window: int = 10
    slow_window: int = 30
    min_trend_gap: float = 0.01
    max_return_volatility: float = 0.055
    max_peak_drawdown: float = 0.15

    def __post_init__(self) -> None:
        if self.lookback < 5:
            raise ValueError("regime lookback must be at least 5")
        if self.fast_window < 2:
            raise ValueError("fast_window must be at least 2")
        if self.slow_window <= self.fast_window:
            raise ValueError("slow_window must be greater than fast_window")
        if self.lookback < self.slow_window:
            raise ValueError("lookback must be at least slow_window")
        if self.min_trend_gap < 0:
            raise ValueError("min_trend_gap cannot be negative")
        if self.max_return_volatility <= 0:
            raise ValueError("max_return_volatility must be positive")
        if not 0 < self.max_peak_drawdown < 1:
            raise ValueError("max_peak_drawdown must be between 0 and 1")


@dataclass(frozen=True)
class RegimeSnapshot:
    name: str
    suitable: bool
    reason: str
    trend_gap: float
    return_volatility: float
    peak_drawdown: float
    fast_average: float
    slow_average: float
    latest_close: float


def classify_regime(candles: list[Candle], config: RegimeFilterConfig | None = None) -> RegimeSnapshot:
    config = config or RegimeFilterConfig()
    required = max(config.lookback, config.slow_window)
    if len(candles) < required:
        latest = candles[-1].close if candles else 0.0
        return RegimeSnapshot(
            name="insufficient_history",
            suitable=False,
            reason=f"Need at least {required} candles for regime classification.",
            trend_gap=0.0,
            return_volatility=0.0,
            peak_drawdown=0.0,
            fast_average=latest,
            slow_average=latest,
            latest_close=latest,
        )

    recent = candles[-config.lookback :]
    closes = [candle.close for candle in recent]
    latest = closes[-1]
    fast_average = sum(closes[-config.fast_window :]) / config.fast_window
    slow_average = sum(closes[-config.slow_window :]) / config.slow_window
    trend_gap = fast_average / max(slow_average, 1e-12) - 1.0

    returns = [
        closes[index] / max(closes[index - 1], 1e-12) - 1.0
        for index in range(1, len(closes))
    ]
    volatility = pstdev(returns) if len(returns) >= 2 else 0.0
    peak = max(closes)
    peak_drawdown = max(0.0, (peak - latest) / max(peak, 1e-12))

    if volatility >= config.max_return_volatility or peak_drawdown >= config.max_peak_drawdown:
        return RegimeSnapshot(
            name="high_volatility_or_drawdown",
            suitable=False,
            reason=(
                f"Risk-off regime: volatility={volatility:.2%}, "
                f"peak_drawdown={peak_drawdown:.2%}."
            ),
            trend_gap=trend_gap,
            return_volatility=volatility,
            peak_drawdown=peak_drawdown,
            fast_average=fast_average,
            slow_average=slow_average,
            latest_close=latest,
        )

    if trend_gap >= config.min_trend_gap and latest >= slow_average:
        name = "bull_trending_up"
        suitable = True
        reason = f"Positive trend gap={trend_gap:.2%} with controlled volatility={volatility:.2%}."
    elif trend_gap <= -config.min_trend_gap and latest < slow_average:
        name = "bear_trending_down"
        suitable = False
        reason = f"Negative trend gap={trend_gap:.2%}; long-only entries are blocked."
    else:
        name = "sideways_low_volatility"
        suitable = True
        reason = f"Sideways regime with controlled volatility={volatility:.2%}."

    return RegimeSnapshot(
        name=name,
        suitable=suitable,
        reason=reason,
        trend_gap=trend_gap,
        return_volatility=volatility,
        peak_drawdown=peak_drawdown,
        fast_average=fast_average,
        slow_average=slow_average,
        latest_close=latest,
    )


def strategy_name(strategy: Strategy | str) -> str:
    if isinstance(strategy, str):
        return strategy
    if isinstance(strategy, MomentumVolumeStrategy):
        return "momentum"
    if isinstance(strategy, BreakoutStrategy):
        return "breakout"
    if isinstance(strategy, MeanReversionStrategy):
        return "mean_reversion"
    return strategy.__class__.__name__.lower()


def regime_allows_strategy(strategy: Strategy | str, snapshot: RegimeSnapshot) -> bool:
    name = strategy_name(strategy)
    if snapshot.name in {"insufficient_history", "high_volatility_or_drawdown", "bear_trending_down"}:
        return False
    if name in {"momentum", "breakout"}:
        return snapshot.name == "bull_trending_up"
    if name == "mean_reversion":
        return snapshot.name == "sideways_low_volatility"
    return snapshot.suitable
