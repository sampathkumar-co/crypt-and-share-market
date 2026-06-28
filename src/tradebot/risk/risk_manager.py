from dataclasses import dataclass
from tradebot.models import Candle, RiskDecision, Signal, Action, Market
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
    def __init__(self, config: RiskConfig | None = None, cost_engine: CostEngine | None = None, tax_engine: TaxEngine | None = None):
        self.config = config or RiskConfig(); self.cost_engine = cost_engine or CostEngine(); self.tax_engine = tax_engine or TaxEngine()
    def evaluate(self, market: Market, cash: float, symbol: str, signal: Signal, candle: Candle, daily_loss: float = 0.0) -> RiskDecision:
        if signal.action != Action.BUY: return RiskDecision(False, reason="Only BUY signals open paper positions")
        if daily_loss <= -cash * self.config.max_daily_loss: return RiskDecision(False, reason="Daily loss limit reached")
        if candle.volume < self.config.min_volume: return RiskDecision(False, reason="Rejected low volume / liquidity setup")
        entry = candle.close; stop = entry * (1 - self.config.stop_loss_pct); target = entry * (1 + self.config.target_pct)
        rr = (target - entry) / max(entry - stop, 1e-9)
        if rr < self.config.min_risk_reward: return RiskDecision(False, reason="Poor risk/reward")
        risk_cash = cash * self.config.risk_per_trade
        qty_by_risk = risk_cash / max(entry - stop, 1e-9)
        qty_by_cap = (cash * self.config.max_position_capital) / entry
        qty = max(0.0, min(qty_by_risk, qty_by_cap))
        gross = (target - entry) * qty
        costs = self.cost_engine.estimate(market, entry, target, qty)
        tax = self.tax_engine.estimate(market, gross)["tax"]
        net_pct = (gross - costs["total_cost"] - tax) / max(entry * qty, 1e-9)
        if net_pct < self.config.min_expected_net_pct: return RiskDecision(False, reason="Expected net profit after fees/tax too small")
        warnings = ("Paper trading only; no real orders are placed",) if signal.risk_score > .75 else ()
        return RiskDecision(True, qty, stop, target, "Approved by paper risk rules", warnings)
