from __future__ import annotations

from dataclasses import dataclass
from math import exp
from typing import Mapping


REGIMES = (
    "strong_trend",
    "weak_trend",
    "low_vol_chop",
    "high_vol_chop",
    "panic",
    "recovery",
)


@dataclass(frozen=True)
class RegimeInputs:
    market_return_30: float
    market_return_90: float
    realized_volatility_30: float
    breadth_50: float
    drawdown_90: float
    correlation_30: float

    def __post_init__(self) -> None:
        if self.realized_volatility_30 < 0:
            raise ValueError("volatility must be non-negative")
        if not 0 <= self.breadth_50 <= 1:
            raise ValueError("breadth must be in [0, 1]")
        if not 0 <= self.drawdown_90 <= 1:
            raise ValueError("drawdown must be in [0, 1]")
        if not -1 <= self.correlation_30 <= 1:
            raise ValueError("correlation must be in [-1, 1]")


def _softmax(scores: Mapping[str, float]) -> dict[str, float]:
    highest = max(scores.values())
    weights = {name: exp(value - highest) for name, value in scores.items()}
    total = sum(weights.values())
    return {name: value / total for name, value in weights.items()}


def regime_probabilities(inputs: RegimeInputs) -> dict[str, float]:
    trend = 0.65 * inputs.market_return_30 + 0.35 * inputs.market_return_90
    high_vol = inputs.realized_volatility_30
    scores = {
        "strong_trend": 18 * trend + 2.0 * (inputs.breadth_50 - 0.5) - high_vol,
        "weak_trend": 8 * abs(trend) + 0.5 * inputs.breadth_50 - 0.5 * high_vol,
        "low_vol_chop": -12 * abs(trend) - 2.0 * high_vol + 0.5 * (1 - inputs.correlation_30),
        "high_vol_chop": -7 * abs(trend) + 2.5 * high_vol + 0.5 * inputs.correlation_30,
        "panic": 5.0 * inputs.drawdown_90 + 3.5 * high_vol - 12 * trend + inputs.correlation_30,
        "recovery": -2.0 * inputs.drawdown_90 + 10 * inputs.market_return_30 - 2 * inputs.market_return_90 + inputs.breadth_50,
    }
    return _softmax(scores)


def dominant_regime(probabilities: Mapping[str, float]) -> str:
    if set(probabilities) != set(REGIMES):
        raise ValueError("probabilities must contain every v5 regime")
    if any(value < 0 for value in probabilities.values()):
        raise ValueError("probabilities cannot be negative")
    if abs(sum(probabilities.values()) - 1.0) > 1e-8:
        raise ValueError("probabilities must sum to one")
    return max(probabilities, key=probabilities.__getitem__)
