from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Mapping, Sequence

from .allocation import EngineForecast


@dataclass(frozen=True)
class AssetHistory:
    closes: tuple[float, ...]
    volumes: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if any(value <= 0 for value in self.closes):
            raise ValueError("closes must be positive")
        if self.volumes and len(self.volumes) != len(self.closes):
            raise ValueError("volumes must align with closes")


def _returns(prices: Sequence[float], period: int) -> float:
    return prices[-1] / prices[-1 - period] - 1.0


def trend_engine(history: AssetHistory) -> EngineForecast:
    if len(history.closes) < 201:
        return EngineForecast(0.0, 1.0, 1.0, 0.0)
    horizons = [_returns(history.closes, period) for period in (7, 30, 90, 200)]
    daily = [history.closes[index] / history.closes[index - 1] - 1.0 for index in range(len(history.closes) - 30, len(history.closes))]
    volatility = max(pstdev(daily), 1e-6)
    agreement = sum(value > 0 for value in horizons) / len(horizons)
    expected = mean(horizons[:3]) / 3.0
    downside = min(1.0, max(0.0, 0.5 - expected / (8 * volatility)))
    uncertainty = min(1.0, pstdev(horizons) / max(abs(mean(horizons)), 1e-6))
    return EngineForecast(expected, downside, uncertainty, agreement)


def mean_reversion_engine(history: AssetHistory) -> EngineForecast:
    if len(history.closes) < 61:
        return EngineForecast(0.0, 1.0, 1.0, 0.0)
    returns = [history.closes[index] / history.closes[index - 1] - 1.0 for index in range(len(history.closes) - 30, len(history.closes))]
    center = mean(returns)
    volatility = max(pstdev(returns), 1e-6)
    latest = returns[-1]
    z = (latest - center) / volatility
    recovery = max(0.0, min(1.0, (-z - 1.5) / 3.0))
    expected = recovery * volatility * 0.5
    return EngineForecast(expected, 1.0 - recovery, min(1.0, 1.0 / (1.0 + abs(z))), recovery)


def volatility_expansion_engine(history: AssetHistory) -> EngineForecast:
    if len(history.closes) < 61:
        return EngineForecast(0.0, 1.0, 1.0, 0.0)
    daily = [history.closes[index] / history.closes[index - 1] - 1.0 for index in range(len(history.closes) - 60, len(history.closes))]
    recent = pstdev(daily[-7:])
    prior = max(pstdev(daily[-30:-7]), 1e-6)
    expansion = recent / prior
    direction = _returns(history.closes, 7)
    reliability = max(0.0, min(1.0, (expansion - 1.0) / 2.0))
    expected = max(0.0, direction) * reliability
    return EngineForecast(expected, 1.0 - reliability, min(1.0, abs(expansion - 1.0) / 3.0), reliability)


def run_independent_engines(histories: Mapping[str, AssetHistory]) -> dict[str, dict[str, EngineForecast]]:
    return {
        asset: {
            "trend": trend_engine(history),
            "mean_reversion": mean_reversion_engine(history),
            "volatility_expansion": volatility_expansion_engine(history),
        }
        for asset, history in histories.items()
    }
