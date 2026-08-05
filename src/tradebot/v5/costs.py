from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """Deterministic one-way execution-cost model, expressed in basis points."""

    fee_bps: float = 10.0
    base_slippage_bps: float = 5.0
    spread_bps: float = 5.0
    stress_multiplier: float = 2.0

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.stress_multiplier < 1:
            raise ValueError("stress_multiplier must be at least one")

    def one_way_bps(self, *, liquidity_score: float = 1.0, stressed: bool = False) -> float:
        if not 0 < liquidity_score <= 1:
            raise ValueError("liquidity_score must be in (0, 1]")
        slippage = self.base_slippage_bps / liquidity_score
        total = self.fee_bps + self.spread_bps / 2.0 + slippage
        return total * (self.stress_multiplier if stressed else 1.0)

    def traded_notional_cost(
        self,
        traded_notional: float,
        *,
        liquidity_score: float = 1.0,
        stressed: bool = False,
    ) -> float:
        if traded_notional < 0:
            raise ValueError("traded_notional must be non-negative")
        return traded_notional * self.one_way_bps(
            liquidity_score=liquidity_score, stressed=stressed
        ) / 10_000.0

    def net_return(
        self,
        gross_return: float,
        *,
        turnover: float,
        liquidity_score: float = 1.0,
        stressed: bool = False,
    ) -> float:
        if turnover < 0:
            raise ValueError("turnover must be non-negative")
        return gross_return - turnover * self.one_way_bps(
            liquidity_score=liquidity_score, stressed=stressed
        ) / 10_000.0
