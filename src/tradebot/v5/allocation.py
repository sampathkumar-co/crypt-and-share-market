from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class EngineForecast:
    expected_return: float
    downside_probability: float
    uncertainty: float
    reliability: float

    def __post_init__(self) -> None:
        for name in ("downside_probability", "uncertainty", "reliability"):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be in [0, 1]")


@dataclass(frozen=True)
class AllocationDecision:
    weights: dict[str, float]
    cash_weight: float
    usable_edges: dict[str, float]
    reason: str


def usable_edge(
    forecast: EngineForecast,
    *,
    expected_cost: float,
    disagreement: float,
) -> float:
    if expected_cost < 0 or not 0 <= disagreement <= 1:
        raise ValueError("invalid cost or disagreement")
    return (
        forecast.expected_return
        - expected_cost
        - 0.5 * forecast.uncertainty
        - 0.5 * forecast.downside_probability
        - 0.25 * disagreement
    ) * forecast.reliability


def allocate(
    forecasts: Mapping[str, Mapping[str, EngineForecast]],
    regime_probabilities: Mapping[str, float],
    engine_regime_weights: Mapping[str, Mapping[str, float]],
    *,
    expected_cost: float,
    disagreement: float,
    maximum_total_exposure: float = 0.10,
    maximum_asset_exposure: float = 0.05,
    panic_threshold: float = 0.45,
) -> AllocationDecision:
    if not forecasts:
        return AllocationDecision({}, 1.0, {}, "no_forecasts")
    if maximum_total_exposure <= 0 or maximum_asset_exposure <= 0:
        raise ValueError("exposure limits must be positive")
    if regime_probabilities.get("panic", 0.0) >= panic_threshold:
        return AllocationDecision({}, 1.0, {}, "panic_forces_cash")

    edges: dict[str, float] = {}
    for asset, engines in forecasts.items():
        weighted = 0.0
        weight_total = 0.0
        for engine, forecast in engines.items():
            regime_weight = sum(
                regime_probabilities.get(regime, 0.0)
                * engine_regime_weights.get(engine, {}).get(regime, 0.0)
                for regime in regime_probabilities
            )
            if regime_weight <= 0:
                continue
            weighted += usable_edge(
                forecast,
                expected_cost=expected_cost,
                disagreement=disagreement,
            ) * regime_weight
            weight_total += regime_weight
        edges[asset] = weighted / weight_total if weight_total else float("-inf")

    positive = [(asset, edge) for asset, edge in edges.items() if edge > 0]
    positive.sort(key=lambda item: item[1], reverse=True)
    selected = positive[:2]
    if not selected:
        return AllocationDecision({}, 1.0, edges, "no_positive_net_edge")

    total_edge = sum(edge for _, edge in selected)
    weights = {
        asset: min(maximum_asset_exposure, maximum_total_exposure * edge / total_edge)
        for asset, edge in selected
    }
    total = sum(weights.values())
    if total > maximum_total_exposure:
        scale = maximum_total_exposure / total
        weights = {asset: value * scale for asset, value in weights.items()}
    return AllocationDecision(weights, 1.0 - sum(weights.values()), edges, "allocated")
