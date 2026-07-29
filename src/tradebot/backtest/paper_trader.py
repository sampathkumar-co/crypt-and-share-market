from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
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
from tradebot.backtest.regime import RegimeFilterConfig, classify_regime, regime_allows_strategy
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
    min_holding_bars: int = 2
    max_holding_bars: int = 40
    cooldown_bars: int = 1
    exit_confirmation_bars: int = 2
    trailing_stop_pct: float = 0.03
    breakeven_trigger_pct: float = 0.02
    use_regime_filter: bool = False
    regime_filter: RegimeFilterConfig = field(default_factory=RegimeFilterConfig)

    def __post_init__(self) -> None:
        if self.warmup_bars < 1:
            raise ValueError("warmup_bars must be positive")
        if self.intrabar_policy not in {"worst_case", "best_case"}:
            raise ValueError("intrabar_policy must be 'worst_case' or 'best_case'")
        if self.min_holding_bars < 0:
            raise ValueError("min_holding_bars cannot be negative")
        if self.max_holding_bars <= self.min_holding_bars:
            raise ValueError("max_holding_bars must be greater than min_holding_bars")
        if self.cooldown_bars < 0:
            raise ValueError("cooldown_bars cannot be negative")
        if self.exit_confirmation_bars < 1:
            raise ValueError("exit_confirmation_bars must be positive")
        if not 0 < self.trailing_stop_pct < 1:
            raise ValueError("trailing_stop_pct must be between 0 and 1")
        if not 0 <= self.breakeven_trigger_pct < 1:
            raise ValueError("breakeven_trigger_pct must be between 0 and 1")


class PaperTrader:
    """Single-position, long-only paper backtester with next-bar execution."""

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
        if not candles:
            raise ValueError("At least one candle is required")
        if trade_start_index < 0 or trade_start_index > len(candles):
            raise ValueError("trade_start_index must be within the candle history")

        position: Position | None = None
        entry_index = -1
        last_exit_index = -10**9
        highest_completed_high = 0.0
        exit_streak = 0
        trades: list[Trade] = []
        rejected: list[str] = []
        curve = [self.cash]
        warnings = [
            "Signals use completed candles and are executed at the next bar open.",
            f"Ambiguous stop/target candles use {self.config.intrabar_policy} ordering.",
            (
                "Regime filter is enabled; unsuitable long-only regimes remain in cash."
                if self.config.use_regime_filter
                else "Regime filter is disabled for this run."
            ),
        ]
        active_bars = 0
        processed_bars = 0
        regime_rejections = 0
        start = max(self.config.warmup_bars, 1)

        for index in range(start, len(candles)):
            candle = candles[index]
            signal_history = candles[:index]
            signal_candle = candles[index - 1]
            can_trade = index >= trade_start_index
            if can_trade:
                processed_bars += 1

            if position is not None:
                if can_trade:
                    active_bars += 1
                bars_held = index - entry_index
                self._advance_protective_stop(position, highest_completed_high)

                exit_condition = False
                exit_condition_reason = ""
                if can_trade and bars_held >= self.config.min_holding_bars:
                    signal = self.strategy.generate_signal(signal_history)
                    if signal.action == Action.SELL:
                        exit_condition = True
                        exit_condition_reason = "Confirmed strategy sell"
                    if self.config.use_regime_filter:
                        snapshot = classify_regime(signal_history, self.config.regime_filter)
                        if not regime_allows_strategy(self.strategy, snapshot):
                            exit_condition = True
                            exit_condition_reason = f"Confirmed regime exit: {snapshot.name}"

                exit_streak = exit_streak + 1 if exit_condition else 0
                exit_price, exit_reason = self._exit_decision(
                    position,
                    candle,
                    bars_held=bars_held,
                    exit_streak=exit_streak,
                    exit_condition_reason=exit_condition_reason,
                )
                if exit_reason:
                    trade = self._close_trade(
                        symbol,
                        position,
                        candle.timestamp,
                        exit_price,
                        exit_reason,
                        holding_bars=bars_held,
                    )
                    self.cash += position.entry_price * position.quantity + trade.net_pnl
                    trades.append(trade)
                    position = None
                    entry_index = -1
                    last_exit_index = index
                    highest_completed_high = 0.0
                    exit_streak = 0
                    curve.append(self.cash)
                    continue

                highest_completed_high = max(highest_completed_high, candle.high)
                curve.append(self.cash + position.quantity * candle.close)
                continue

            if not can_trade:
                curve.append(self.cash)
                continue

            if index - last_exit_index <= self.config.cooldown_bars:
                curve.append(self.cash)
                continue

            if self.config.use_regime_filter:
                snapshot = classify_regime(signal_history, self.config.regime_filter)
                if not regime_allows_strategy(self.strategy, snapshot):
                    regime_rejections += 1
                    curve.append(self.cash)
                    continue

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
                    entry_index = index
                    highest_completed_high = candle.open
                    warnings.extend(decision.warnings)

                    immediate_price, immediate_reason = self._intrabar_stop_target(position, candle)
                    if immediate_reason:
                        trade = self._close_trade(
                            symbol,
                            position,
                            candle.timestamp,
                            immediate_price,
                            immediate_reason,
                            holding_bars=0,
                        )
                        self.cash += position.entry_price * position.quantity + trade.net_pnl
                        trades.append(trade)
                        position = None
                        entry_index = -1
                        last_exit_index = index
                        highest_completed_high = 0.0
                        curve.append(self.cash)
                        continue

                    highest_completed_high = max(highest_completed_high, candle.high)
                else:
                    rejected.append(f"{candle.timestamp.isoformat()} {symbol}: Insufficient cash after sizing")
            elif signal.action == Action.BUY:
                rejected.append(f"{candle.timestamp.isoformat()} {symbol}: {decision.reason}")

            curve.append(self.cash if position is None else self.cash + position.quantity * candle.close)

        if position is not None:
            last = candles[-1]
            holding_bars = max(0, len(candles) - 1 - entry_index)
            trade = self._close_trade(
                symbol,
                position,
                last.timestamp,
                last.close,
                "End of backtest",
                holding_bars=holding_bars,
            )
            self.cash += position.entry_price * position.quantity + trade.net_pnl
            trades.append(trade)
            curve.append(self.cash)

        pnls = [trade.net_pnl for trade in trades]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [pnl for pnl in pnls if pnl <= 0]
        gross_return = sum(trade.gross_pnl for trade in trades) / self.starting_cash
        net_return = (self.cash - self.starting_cash) / self.starting_cash
        drawdown = max_drawdown(curve)

        benchmark_index = max(start, trade_start_index)
        if benchmark_index >= len(candles):
            benchmark_index = len(candles) - 1
        first_executable = candles[benchmark_index].open
        buy_and_hold = (candles[-1].close - first_executable) / first_executable if first_executable > 0 else 0.0

        returns = period_returns(curve)
        annualization = self.config.annualization or (365 if self.market == Market.CRYPTO else 252)
        total_fees = sum(trade.fees for trade in trades)
        total_slippage = sum(trade.slippage_cost for trade in trades)
        total_tax = sum(trade.tax_estimate for trade in trades)
        total_costs = total_fees + total_slippage + total_tax
        gross_activity = sum(abs(trade.gross_pnl) for trade in trades)
        turnover_notional = sum(
            (trade.entry_price + trade.exit_price) * trade.quantity
            for trade in trades
        )
        holding_bars = [trade.holding_bars for trade in trades]

        result = BacktestResult(
            starting_cash=self.starting_cash,
            ending_cash=self.cash,
            gross_return=gross_return,
            net_return=net_return,
            win_rate=win_rate(pnls),
            max_drawdown=drawdown,
            total_fees=total_fees,
            total_tax=total_tax,
            trades=trades,
            rejected_signals=rejected,
            equity_curve=curve,
            average_win=sum(wins) / len(wins) if wins else 0.0,
            average_loss=sum(losses) / len(losses) if losses else 0.0,
            risk_warnings=list(dict.fromkeys(warnings)),
            total_slippage=total_slippage,
            total_tds_cashflow=sum(trade.tds_cashflow for trade in trades),
            cash_return=0.0,
            buy_and_hold_return=buy_and_hold,
            excess_return=net_return - buy_and_hold,
            profit_factor=profit_factor(pnls),
            expectancy=expectancy(pnls),
            sharpe_ratio=sharpe_ratio(returns, annualization),
            sortino_ratio=sortino_ratio(returns, annualization),
            calmar_ratio=net_return / drawdown if drawdown > 0 else 0.0,
            exposure=active_bars / processed_bars if processed_bars else 0.0,
            average_holding_bars=sum(holding_bars) / len(holding_bars) if holding_bars else 0.0,
            trades_per_100_bars=len(trades) / max(processed_bars, 1) * 100.0,
            turnover=turnover_notional / self.starting_cash,
            cost_drag_ratio=total_costs / max(gross_activity, total_costs, 1e-9) if trades else 0.0,
            regime_rejections=regime_rejections,
        )
        if self.store_path:
            self._store(result)
        return result

    def _advance_protective_stop(self, position: Position, highest_completed_high: float) -> None:
        if highest_completed_high <= position.entry_price:
            return
        if highest_completed_high >= position.entry_price * (1.0 + self.config.breakeven_trigger_pct):
            trailing = highest_completed_high * (1.0 - self.config.trailing_stop_pct)
            position.stop_loss = max(position.stop_loss, position.entry_price, trailing)

    def _intrabar_stop_target(self, position: Position, candle: Candle) -> tuple[float, str]:
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
        return 0.0, ""

    def _exit_decision(
        self,
        position: Position,
        candle: Candle,
        *,
        bars_held: int,
        exit_streak: int,
        exit_condition_reason: str,
    ) -> tuple[float, str]:
        price, reason = self._intrabar_stop_target(position, candle)
        if reason:
            return price, reason
        if bars_held >= self.config.max_holding_bars:
            return candle.open, "Maximum holding period reached"
        if (
            bars_held >= self.config.min_holding_bars
            and exit_streak >= self.config.exit_confirmation_bars
        ):
            return candle.open, exit_condition_reason or "Confirmed exit condition"
        return 0.0, ""

    def _close_trade(
        self,
        symbol: str,
        position: Position,
        exit_time: datetime,
        exit_price: float,
        reason: str,
        *,
        holding_bars: int,
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
            holding_bars=holding_bars,
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

        temporary = self.store_path.with_suffix(self.store_path.suffix + ".tmp")
        temporary.write_text(json.dumps(asdict(result), default=default, indent=2), encoding="utf-8")
        temporary.replace(self.store_path)
