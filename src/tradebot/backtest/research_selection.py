from __future__ import annotations

from typing import Any, Sequence

from tradebot.models import Market


def balanced_candidate_pairs(
    parameter_sets: Sequence[dict[str, Any]],
    execution_profiles: Sequence[dict[str, Any]],
    max_candidates: int,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Return a deterministic candidate budget without prefix bias.

    The first pass gives every parameter set at most one execution profile before
    any parameter set receives a second profile. Later passes rotate profiles so
    a bounded budget covers both parameter and execution dimensions.
    """
    if max_candidates < 1:
        raise ValueError("max_candidates must be positive")
    if not parameter_sets or not execution_profiles:
        return []

    budget = min(max_candidates, len(parameter_sets) * len(execution_profiles))
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for pass_index in range(len(execution_profiles)):
        for parameter_index, parameters in enumerate(parameter_sets):
            profile_index = (parameter_index + pass_index) % len(execution_profiles)
            pairs.append((dict(parameters), dict(execution_profiles[profile_index])))
            if len(pairs) == budget:
                return pairs
    return pairs


def required_warmup_bars(
    strategy_lookback: int,
    *,
    regime_lookback: int = 30,
    minimum: int = 10,
) -> int:
    """Return enough completed history for strategy and regime calculations."""
    if strategy_lookback < 0 or regime_lookback < 0 or minimum < 0:
        raise ValueError("warmup inputs cannot be negative")
    return max(minimum, strategy_lookback + 1, regime_lookback)


def metrics_are_active(metrics: dict[str, float | int]) -> bool:
    """A selected candidate is deployed only when unseen trades occurred."""
    return int(metrics.get("trades", 0)) > 0


def annualization_for_market(market: Market) -> int:
    """Return the shared annualization convention used by research metrics."""
    return 365 if market == Market.CRYPTO else 252
