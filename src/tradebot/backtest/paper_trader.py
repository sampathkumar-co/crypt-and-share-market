from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from tradebot.backtest.metrics import (
    expectancy,
    max_drawdown,
    period_returns,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
    win_rate,
)
from tradebot.models import Action, BacktestResult, Candle, Market, Position, Trade
from tradebot.risk.cost_engine import CostEngine
from tradebot.risk.risk_manager import RiskManager
from tradebot.risk.tax_engine import TaxEngine
from tradebot.strategies.base import Strategy


@dataclass(frozen=True)
class BacktestConfig:
    warmup_bars: int = 10
    intrabar_policy: str = "worst_case"
    annualization: int | None = None

    def __post_init__(self) -> None:
        if self.warmup_bars < 1:
            raise ValueError("warmup_bars must be positive")
        if self.intrabar_policy not in {"worst_case", "best_case"}:
            raise ValueError("intrabar_policy must be 'worst_case' or 'best_case'")


class PaperTrader:
    """Single-position, long-only paper backtester with next-bar execution.

    A strategy sees candles only through bar ``i - 1`` and any resulting order is
    filled at bar ``i`` open. This prevents the common look-ahead error of using a
    closing price both to create and fill a signal on the same candle.
    """

    def __init__(
        self,
        market: Market,
        strategy: Strategy,
        starting_cash: float = 100000.0,
        store_path: str | None = None,
        config: BacktestConfig | None = None,
    ):
        if starting_cash <= 0:
            raise ValueError("starting_cash must be positive")
        self.market = market
        self.strategy = strategy
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.risk = RiskManager()
        self.costs = CostEngine()
        self.tax = TaxEngine()
        self.store_path = Path(store_path) if store_path else None
        self.config = config or BacktestConfig()

    def run(self, symbol: str, candles: list[Candle], *, trade_start_index: int = 0) -> BacktestResult:
        self.cash = self.starting_cash
        position: Position | None = None
        trades: list[Trade] = []
        rejected: list[str] = []
        curve = [self.cash]
        warnings = [
            "Signals use completed candles and are executed at the next bar open.",
            f"Ambiguous stop/target candles use {self.config.intrabar_policy} ordering.",
        ]
        active_bars = 0
        processed_bars = 0
        start = max(self.config.warmup_bars, 1)

        for index in range(start, len(candles)):
            candle = candles[index]
            signal_history = candles[:index]
            signal_candle = candles[index - 1]
            can_trade = index >= trade_start_index
            processed_bars += 1

            if position is not None:
                active_bars += 1
                exit_price, exit_reason = self._exit_decision(position, candle, signal_history, can_trade)
                if exit_reason:
                    trade = self._close_trade(symbol, position, candle.timestamp, exit_price, exit_reason)
                    self.cash += position.entry_price * position.quantity + trade.net_pnl
                    trades.append(trade)
                    position = None
                    curve.append(self.cash)
                    continue
                curve.append(self.cash + (candle.close - position.entry_price) * position.quantity + position.entry_price * position.quantity)
                continue

            if can_trade:
                signal = self.strategy.generate_signal(signal_history)
                decision = self.risk.evaluate(
                    self.market,
                    self.cash,
                    symbol,
                    signal,
                    signal_candle,
                    entry_price=candle.open,
                )
                if decision.approved:
                    notional = candle.open * decision.quantity
                    if notional <= self.cash:
                        self.cash -= notional
                        position = Position(
                            symbol,
                            decision.quantity,
                            candle.open,
                            decision.stop_loss,
                            decision.target,
                            candle.timestamp,
                        )
                        warnings.extend(decision.warnings)
                    else:
                        rejected.append(f"{candle.timestamp.isoformat()} {symbol}: Insufficient cash after sizing")
                elif signal.action == Action.BUY:
                    rejected.append(f"{candle.timestamp.isoformat()} {symbol}: {decision.reason}")

            curve.append(self.cash if position is None else self.cash + position.quantity * candle.close)

        if position is not None:
            last = candles[-1]
            trade = self._close_trade(symbol, position, last.timestamp, last.close, "End of backtest")
            self.cash += position.entry_price * position.quantity + trade.net_pnl
            trades.append(trade)
            curve.append(self.cash)

        pnls = [trade.net_pnl for trade in trades]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [pnl for pnl in pnls if pnl <= 0]
        gross_return = sum(trade.gross_pnl for trade in trades) / self.starting_cash
        net_return = (self.cash - self.starting_cash) / self.starting_cash
        drawdown = max_drawdown(curve)
        first_executable = candles[start].open if len(candles) > start else candles[0].open if candles else 0.0
        buy_and_hold = (candles[-1].close - first_executable) / first_executable if first_executable > 0 and candles else 0.0
        returns = period_returns(curve)
        annualization = self.config.annualization or (365 if self.market == Market.CRYPTO else 252)

        result = BacktestResult(
            starting_cash=self.starting_cash,
            ending_cash=self.cash,
            gross_return=gross_return,
            net_return=net_return,
            win_rate=win_rate(pnls),
            max_drawdown=drawdown,
            total_fees=sum(trade.fees for trade in trades),
            total_tax=sum(trade.tax_estimate for trade in trades),
            trades=trades,
            rejected_signals=rejected,
            equity_curve=curve,
            average_win=sum(wins) / len(wins) if wins else 0.0,
            average_loss=sum(losses) / len(losses) if losses else 0.0,
            risk_warnings=list(dict.fromkeys(warnings)),
            total_slippage=sum(trade.slippage_cost for trade in trades),
            total_tds_cashflow=sum(trade.tds_cashflow for trade in trades),
            buy_and_hold_return=buy_and_hold,
            excess_return=net_return - buy_and_hold,
            profit_factor=profit_factor(pnls),
            expectancy=expectancy(pnls),
            sharpe_ratio=sharpe_ratio(returns, annualization),
            sortino_ratio=sortino_ratio(returns, annualization),
            calmar_ratio=net_return / drawdown if drawdown > 0 else 0.0,
            exposure=active_bars / processed_bars if processed_bars else 0.0,
        )
        if self.store_path:
            self._store(result)
        return result

    def _exit_decision(
        self,
        position: Position,
        candle: Candle,
        signal_history: list[Candle],
        can_trade: bool,
    ) -> tuple[float, str]:
        if candle.open <= position.stop_loss:
            return candle.open, "Stop loss gap exit"
        if candle.open >= position.target:
            return candle.open, "Target gap exit"

        stop_hit = candle.low <= position.stop_loss
        target_hit = candle.high >= position.target
        if stop_hit and target_hit:
            if self.config.intrabar_policy == "best_case":
                return position.target, "Target hit before stop (best-case policy)"
            return position.stop_loss, "Stop hit before target (worst-case policy)"
        if stop_hit:
            return position.stop_loss, "Stop loss hit"
        if target_hit:
            return position.target, "Target hit"
        if can_trade and self.strategy.generate_signal(signal_history).action == Action.SELL:
            return candle.open, "Strategy sell at next open"
        return 0.0, ""

    def _close_trade(
        self,
        symbol: str,
        position: Position,
        exit_time: datetime,
        exit_price: float,
        reason: str,
    ) -> Trade:
        gross = (exit_price - position.entry_price) * position.quantity
        costs = self.costs.estimate(self.market, position.entry_price, exit_price, position.quantity)
        holding_days = max(0, (exit_time.date() - position.entry_time.date()).days)
        tax_result = self.tax.estimate(
            self.market,
            gross,
            holding_days,
            exit_value=exit_price * position.quantity,
        )
        tax = float(tax_result["tax"])
        tds = float(tax_result["tds_cashflow"])
        net = gross - costs["fees"] - costs["slippage"] - tax
        return Trade(
            symbol=symbol,
            market=self.market,
            entry_time=position.entry_time,
            exit_time=exit_time,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=position.quantity,
            gross_pnl=gross,
            fees=costs["fees"],
            slippage_cost=costs["slippage"],
            tax_estimate=tax,
            net_pnl=net,
            pnl_percent=net / max(position.entry_price * position.quantity, 1e-9),
            reason=reason,
            tds_cashflow=tds,
            holding_days=holding_days,
        )

    def _store(self, result: BacktestResult) -> None:
        assert self.store_path is not None
        self.store_path.parent.mkdir(parents=True, exist_ok=True)

        def default(value):
            if isinstance(value, datetime):
                return value.isoformat()
            if hasattr(value, "value"):
                return value.value
            return str(value)

        self.store_path.write_text(json.dumps(asdict(result), default=default, indent=2), encoding="utf-8")
