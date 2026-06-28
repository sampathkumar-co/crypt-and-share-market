from __future__ import annotations
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from tradebot.backtest.metrics import max_drawdown, win_rate
from tradebot.models import Action, BacktestResult, Candle, Market, Position, Trade
from tradebot.risk.cost_engine import CostEngine
from tradebot.risk.risk_manager import RiskManager
from tradebot.risk.tax_engine import TaxEngine
from tradebot.strategies.base import Strategy

class PaperTrader:
    def __init__(self, market: Market, strategy: Strategy, starting_cash: float = 100000.0, store_path: str | None = None):
        self.market=market; self.strategy=strategy; self.starting_cash=starting_cash; self.cash=starting_cash
        self.risk=RiskManager(); self.costs=CostEngine(); self.tax=TaxEngine(); self.store_path=Path(store_path) if store_path else None
    def run(self, symbol: str, candles: list[Candle]) -> BacktestResult:
        position: Position | None = None; trades: list[Trade]=[]; rejected: list[str]=[]; curve=[self.cash]; warnings=[]
        for i in range(10, len(candles)):
            candle=candles[i]
            if position:
                exit_reason = None
                if candle.low <= position.stop_loss: exit_price=position.stop_loss; exit_reason="Stop loss hit"
                elif candle.high >= position.target: exit_price=position.target; exit_reason="Target hit"
                elif self.strategy.generate_signal(candles[:i+1]).action == Action.SELL: exit_price=candle.close; exit_reason="Strategy sell"
                else: curve.append(self.cash + (candle.close-position.entry_price)*position.quantity); continue
                gross=(exit_price-position.entry_price)*position.quantity; c=self.costs.estimate(self.market, position.entry_price, exit_price, position.quantity); t=self.tax.estimate(self.market, gross)["tax"]
                net=gross-c["fees"]-c["slippage"]-t; self.cash += position.entry_price*position.quantity + net
                trade=Trade(symbol,self.market,position.entry_time,candle.timestamp,position.entry_price,exit_price,position.quantity,gross,c["fees"],c["slippage"],t,net,net/max(position.entry_price*position.quantity,1e-9),exit_reason)
                trades.append(trade); position=None; curve.append(self.cash); continue
            signal = self.strategy.generate_signal(candles[:i+1])
            decision = self.risk.evaluate(self.market, self.cash, symbol, signal, candle)
            if decision.approved:
                cost = candle.close * decision.quantity; self.cash -= cost
                position = Position(symbol, decision.quantity, candle.close, decision.stop_loss, decision.target, candle.timestamp)
                warnings.extend(decision.warnings)
            elif signal.action == Action.BUY:
                rejected.append(f"{candle.timestamp.isoformat()} {symbol}: {decision.reason}")
            curve.append(self.cash)
        if position:
            last=candles[-1]; gross=(last.close-position.entry_price)*position.quantity; c=self.costs.estimate(self.market, position.entry_price,last.close,position.quantity); t=self.tax.estimate(self.market,gross)["tax"]; net=gross-c["fees"]-c["slippage"]-t; self.cash += position.entry_price*position.quantity+net; trades.append(Trade(symbol,self.market,position.entry_time,last.timestamp,position.entry_price,last.close,position.quantity,gross,c["fees"],c["slippage"],t,net,net/max(position.entry_price*position.quantity,1e-9),"End of backtest")); curve.append(self.cash)
        pnls=[t.net_pnl for t in trades]; wins=[p for p in pnls if p>0]; losses=[p for p in pnls if p<=0]
        result=BacktestResult(self.starting_cash,self.cash,(self.cash-self.starting_cash)/self.starting_cash,(self.cash-self.starting_cash)/self.starting_cash,win_rate(pnls),max_drawdown(curve),sum(t.fees for t in trades),sum(t.tax_estimate for t in trades),trades,rejected,curve,sum(wins)/len(wins) if wins else 0,sum(losses)/len(losses) if losses else 0,warnings)
        if self.store_path: self._store(result)
        return result
    def _store(self, result: BacktestResult) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        def default(o):
            if isinstance(o, datetime): return o.isoformat()
            if hasattr(o, "value"): return o.value
            return str(o)
        self.store_path.write_text(json.dumps(asdict(result), default=default, indent=2), encoding="utf-8")
