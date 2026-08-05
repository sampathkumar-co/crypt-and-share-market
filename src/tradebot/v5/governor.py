from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GovernorState:
    data_stale: bool = False
    source_disagreement: bool = False
    out_of_distribution_score: float = 0.0
    model_disagreement: float = 0.0
    rolling_drawdown: float = 0.0
    realized_slippage_bps: float = 0.0
    assumed_slippage_bps: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "out_of_distribution_score",
            "model_disagreement",
            "rolling_drawdown",
            "realized_slippage_bps",
            "assumed_slippage_bps",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True)
class GovernorDecision:
    force_cash: bool
    exposure_multiplier: float
    reasons: tuple[str, ...]


def evaluate_governor(
    state: GovernorState,
    *,
    maximum_drawdown: float = 0.10,
    disagreement_limit: float = 0.50,
    distribution_shift_limit: float = 0.75,
) -> GovernorDecision:
    reasons: list[str] = []
    if state.data_stale:
        reasons.append("stale_data")
    if state.source_disagreement:
        reasons.append("source_disagreement")
    if state.out_of_distribution_score > distribution_shift_limit:
        reasons.append("distribution_shift")
    if state.model_disagreement > disagreement_limit:
        reasons.append("model_disagreement")
    if state.rolling_drawdown >= maximum_drawdown:
        reasons.append("drawdown_limit")
    if state.assumed_slippage_bps > 0 and state.realized_slippage_bps > 2 * state.assumed_slippage_bps:
        reasons.append("slippage_model_broken")
    if reasons:
        return GovernorDecision(True, 0.0, tuple(reasons))
    drawdown_fraction = min(1.0, state.rolling_drawdown / maximum_drawdown) if maximum_drawdown else 1.0
    multiplier = max(0.25, 1.0 - 0.75 * drawdown_fraction)
    return GovernorDecision(False, multiplier, ())
