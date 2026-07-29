from __future__ import annotations

import math
from dataclasses import dataclass

from tradebot.models import Action, Candle, Market, RiskDecision, Signal
from tradebot.risk.cost_engine import CostEngine
from tradebot.risk.tax_engine import TaxEngine


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 0.01
    max_daily_loss: float = 0.03
    max_position_capital: float = 0.20
    min_risk_reward: float = 1.5
    min_volume: float = 1000.0
    min_expected_net_pct: float = 0.002
    stop_loss_pct: float = 0.02
    target_pct: float = 0.04


class RiskManager:
    def __init__(
        self,
        config: RiskConfig | None = None,
        cost_engine: CostEngine | None = None,
        tax_engine: TaxEngine | None = None,
    ):
        self.config = config or RiskConfig()
        self.cost_engine = cost_engine or CostEngine()
        self.tax_engine = tax_engine or TaxEngine()

    def evaluate(
        self,
        market: Market,
        cash: float,
        symbol: str,
        signal: Signal,
        candle: Candle,
        daily_loss: float = 0.0,
        *,
        entry_price: float | None = None,
    ) -> RiskDecision:
        if signal.action != Action.BUY:
            return RiskDecision(False, reason="Only BUY signals open paper positions")
        if cash <= 0:
            return RiskDecision(False, reason="No cash available")
        if daily_loss <= -cash * self.config.max_daily_loss:
            return RiskDecision(False, reason="Daily loss limit reached")
        if candle.volume < self.config.min_volume:
            return RiskDecision(False, reason="Rejected low volume / liquidity setup")

        entry = float(entry_price if entry_price is not None else candle.close)
        if entry <= 0:
            return RiskDecision(False, reason="Invalid entry price")
        stop = entry * (1 - self.config.stop_loss_pct)
        target = entry * (1 + self.config.target_pct)
        reward_to_risk = (target - entry) / max(entry - stop, 1e-9)
        if reward_to_risk < self.config.min_risk_reward:
            return RiskDecision(False, reason="Poor risk/reward")

        risk_cash = cash * self.config.risk_per_trade
        quantity_by_risk = risk_cash / max(entry - stop, 1e-9)
        quantity_by_cap = (cash * self.config.max_position_capital) / entry
        quantity = max(0.0, min(quantity_by_risk, quantity_by_cap))
        if market == Market.EQUITY:
            quantity = float(math.floor(quantity))
        if quantity <= 0:
            return RiskDecision(False, reason="Position size rounded to zero")

        gross = (target - entry) * quantity
        costs = self.cost_engine.estimate(market, entry, target, quantity)
        tax = float(
            self.tax_engine.estimate(
                market,
                gross,
                exit_value=target * quantity,
            )["tax"]
        )
        net_pct = (gross - costs["total_cost"] - tax) / max(entry * quantity, 1e-9)
        if net_pct < self.config.min_expected_net_pct:
            return RiskDecision(False, reason="Expected net profit after fees/tax too small")

        warnings = ("Paper trading only; no real orders are placed",) if signal.risk_score > 0.75 else ()
        return RiskDecision(True, quantity, stop, target, "Approved by paper risk rules", warnings)
