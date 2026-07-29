from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tradebot.backtest.metrics import max_drawdown, win_rate
from tradebot.backtest.regime import RegimeFilterConfig, classify_regime, regime_allows_strategy
from tradebot.data.csv_loader import load_candles
from tradebot.ml.crypto_signal_model import CryptoSignalModel
from tradebot.models import Action, Candle, Market, Position, Signal
from tradebot.risk.cost_engine import CostEngine
from tradebot.risk.risk_manager import RiskManager
from tradebot.risk.tax_engine import TaxEngine
from tradebot.scanner.crypto_scanner import ScannerConfig, evaluate_symbol


@dataclass
class PortfolioTrade:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    fees: float
    slippage_cost: float
    tax_estimate: float
    net_pnl: float
    pnl_percent: float
    entry_reason: str
    exit_reason: str
    entry_ml_probability: float | None = None
    entry_ml_score: float | None = None
    tds_cashflow: float = 0.0
    holding_bars: int = 0


@dataclass
class PortfolioResult:
    starting_cash: float
    ending_cash: float
    gross_return: float
    net_return: float
    max_drawdown: float
    win_rate: float
    rotations: int
    average_hold_bars: float
    total_fees: float
    total_tax: float
    rejected_opportunities_count: int
    trades: list[PortfolioTrade] = field(default_factory=list)
    equity_curve: list[dict[str, float | str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total_tds_cashflow: float = 0.0
    cash_return: float = 0.0
    buy_and_hold_return: float = 0.0
    excess_vs_buy_and_hold: float = 0.0
    total_slippage: float = 0.0
    cost_drag_ratio: float = 0.0
    turnover: float = 0.0
    trades_per_100_bars: float = 0.0
    regime_filtered_opportunities: int = 0
    cooldown_skips: int = 0


@dataclass(frozen=True)
class PortfolioConfig:
    max_holding_bars: int = 30
    min_holding_bars: int = 3
    cooldown_bars: int = 2
    exit_confirmation_bars: int = 2
    scanner_top: int = 20
    min_symbols: int = 2
    min_candles_per_symbol: int = 30
    danger_risk_score: float = 85.0
    intrabar_policy: str = "worst_case"
    trailing_stop_pct: float = 0.03
    breakeven_trigger_pct: float = 0.02
    min_opportunity_score: float = 55.0
    min_expected_net_percent: float = 0.20
    max_trades_per_100_bars: float = 8.0
    use_regime_filter: bool = True
    regime_filter: RegimeFilterConfig = field(default_factory=RegimeFilterConfig)

    def __post_init__(self) -> None:
        if self.max_holding_bars < 1:
            raise ValueError("max_holding_bars must be positive")
        if self.min_holding_bars < 0 or self.min_holding_bars > self.max_holding_bars:
            raise ValueError("min_holding_bars must be between 0 and max_holding_bars")
        if self.cooldown_bars < 0:
            raise ValueError("cooldown_bars cannot be negative")
        if self.exit_confirmation_bars < 1:
            raise ValueError("exit_confirmation_bars must be positive")
        if self.scanner_top < 1:
            raise ValueError("scanner_top must be positive")
        if self.intrabar_policy not in {"worst_case", "best_case"}:
            raise ValueError("intrabar_policy must be 'worst_case' or 'best_case'")
        if not 0 < self.trailing_stop_pct < 1:
            raise ValueError("trailing_stop_pct must be between 0 and 1")
        if not 0 <= self.breakeven_trigger_pct < 1:
            raise ValueError("breakeven_trigger_pct must be between 0 and 1")
        if not 0 <= self.min_opportunity_score <= 100:
            raise ValueError("min_opportunity_score must be between 0 and 100")
        if self.min_expected_net_percent < 0:
            raise ValueError("min_expected_net_percent cannot be negative")
        if self.max_trades_per_100_bars <= 0:
            raise ValueError("max_trades_per_100_bars must be positive")


class CryptoPortfolioPaperTrader:
    """Paper-only, one-position crypto rotation simulator.

    Candidate ranking uses candles strictly before execution. Entries fill at the
    current open. Regime filtering, minimum holds, exit confirmation, cooldowns,
    trailing protection, and a turnover ceiling reduce unnecessary rotation.
    """

    def __init__(
        self,
        cash: float = 100000.0,
        config: PortfolioConfig | None = None,
        scanner_config: ScannerConfig | None = None,
        model: CryptoSignalModel | None = None,
    ):
        if cash <= 0:
            raise ValueError("cash must be positive")
        self.starting_cash = cash
        self.cash = cash
        self.config = config or PortfolioConfig()
        self.scanner_config = scanner_config or ScannerConfig()
        self.risk = RiskManager()
        self.costs = CostEngine()
        self.tax = TaxEngine()
        self.model = model

    def run_folder(self, folder: str | Path) -> PortfolioResult:
        histories = {
            path.stem: load_candles(path)
            for path in sorted(Path(folder).glob("*.csv"))
        }
        return self.run(histories)

    def run(self, histories: dict[str, list[Candle]]) -> PortfolioResult:
        self.cash = self.starting_cash
        if not histories:
            raise ValueError("No symbol histories supplied")

        warnings = [
            "Signals use completed candles and entries execute at the next available open.",
            f"Ambiguous stop/target candles use {self.config.intrabar_policy} ordering.",
            "Turnover, cooldown, minimum-hold and transaction-cost gates are enabled.",
        ]
        if self.config.use_regime_filter:
            warnings.append("Long-only entries are blocked in unsuitable market regimes.")
        if len(histories) < self.config.min_symbols:
            warnings.append("Too few symbols for diversified rotation research.")
        short_symbols = [
            symbol
            for symbol, candles in histories.items()
            if len(candles) < self.config.min_candles_per_symbol
        ]
        if short_symbols:
            warnings.append(f"Too few candles for symbols: {', '.join(short_symbols)}")

        all_times = sorted(
            {candle.timestamp for candles in histories.values() for candle in candles}
        )
        if not all_times:
            raise ValueError("Symbol histories contain no candles")
        by_symbol_time = {
            symbol: {candle.timestamp: candle for candle in candles}
            for symbol, candles in histories.items()
        }

        position: Position | None = None
        position_symbol = ""
        entry_reason = ""
        entry_index = -1
        last_exit_index = -10**9
        highest_completed_high = 0.0
        exit_streak = 0
        entry_ml_probability: float | None = None
        entry_ml_score: float | None = None

        trades: list[PortfolioTrade] = []
        rejected_count = 0
        regime_filtered = 0
        cooldown_skips = 0
        equity_curve: list[dict[str, float | str]] = []
        day_start_equity = self.cash
        current_day = None
        halted_for_day = False

        for index, timestamp in enumerate(all_times):
            if current_day != timestamp.date():
                current_day = timestamp.date()
                day_start_equity = self._mark_to_market(
                    position, position_symbol, timestamp, by_symbol_time
                )
                halted_for_day = False

            closed_this_bar = False
            if position is not None:
                candle = by_symbol_time.get(position_symbol, {}).get(timestamp)
                if candle is not None:
                    bars_held = index - entry_index
                    self._advance_protective_stop(position, highest_completed_high)
                    history_before_execution = [
                        item
                        for item in histories[position_symbol]
                        if item.timestamp < timestamp
                    ]
                    exit_condition, exit_condition_reason = self._exit_condition(
                        position_symbol,
                        history_before_execution,
                        bars_held,
                    )
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
                            position_symbol,
                            position,
                            timestamp,
                            exit_price,
                            entry_reason,
                            exit_reason,
                            holding_bars=bars_held,
                            entry_ml_probability=entry_ml_probability,
                            entry_ml_score=entry_ml_score,
                        )
                        self.cash += position.entry_price * position.quantity + trade.net_pnl
                        trades.append(trade)
                        position = None
                        position_symbol = ""
                        entry_reason = ""
                        entry_index = -1
                        last_exit_index = index
                        highest_completed_high = 0.0
                        exit_streak = 0
                        entry_ml_probability = None
                        entry_ml_score = None
                        closed_this_bar = True
                        if (
                            trade.net_pnl < 0
                            and abs(trade.net_pnl)
                            >= day_start_equity * self.risk.config.max_daily_loss
                        ):
                            halted_for_day = True
                    else:
                        highest_completed_high = max(
                            highest_completed_high, candle.high
                        )

            equity = self._mark_to_market(
                position, position_symbol, timestamp, by_symbol_time
            )
            if equity <= day_start_equity * (
                1 - self.risk.config.max_daily_loss
            ):
                halted_for_day = True

            if position is None and not halted_for_day and not closed_this_bar:
                if index - last_exit_index <= self.config.cooldown_bars:
                    cooldown_skips += 1
                elif self._trade_rate(trades, index + 1) >= self.config.max_trades_per_100_bars:
                    rejected_count += 1
                else:
                    candidates = []
                    for symbol, candles in histories.items():
                        history_before_execution = [
                            candle for candle in candles if candle.timestamp < timestamp
                        ]
                        execution_candle = by_symbol_time.get(symbol, {}).get(timestamp)
                        if (
                            execution_candle is None
                            or len(history_before_execution)
                            < self.scanner_config.min_candles
                        ):
                            continue

                        if self.config.use_regime_filter:
                            snapshot = classify_regime(
                                history_before_execution,
                                self.config.regime_filter,
                            )
                            if not regime_allows_strategy("momentum", snapshot):
                                regime_filtered += 1
                                continue

                        scan = evaluate_symbol(
                            symbol,
                            Market.CRYPTO,
                            history_before_execution,
                            self.scanner_config,
                            model=self.model,
                        )
                        combined_score = (
                            scan.combined_opportunity_score
                            if scan.combined_opportunity_score is not None
                            else scan.opportunity_score
                        )
                        if scan.rejected:
                            rejected_count += 1
                            continue
                        if combined_score < self.config.min_opportunity_score:
                            rejected_count += 1
                            continue
                        if (
                            scan.estimated_net_profit_after_cost_tax
                            < self.config.min_expected_net_percent
                        ):
                            rejected_count += 1
                            continue
                        candidates.append(
                            (
                                scan,
                                combined_score,
                                history_before_execution[-1],
                                execution_candle,
                            )
                        )

                    candidates.sort(key=lambda item: item[1], reverse=True)
                    for (
                        candidate,
                        combined_score,
                        signal_candle,
                        execution_candle,
                    ) in candidates[: self.config.scanner_top]:
                        risk_signal = Signal(
                            Action.BUY,
                            combined_score / 100.0,
                            candidate.explanation,
                            candidate.confidence,
                            candidate.risk_score / 100.0,
                        )
                        decision = self.risk.evaluate(
                            Market.CRYPTO,
                            self.cash,
                            candidate.symbol,
                            risk_signal,
                            signal_candle,
                            daily_loss=equity - day_start_equity,
                            entry_price=execution_candle.open,
                        )
                        if not decision.approved:
                            rejected_count += 1
                            continue

                        position = Position(
                            candidate.symbol,
                            decision.quantity,
                            execution_candle.open,
                            decision.stop_loss,
                            decision.target,
                            timestamp,
                        )
                        position_symbol = candidate.symbol
                        entry_index = index
                        highest_completed_high = execution_candle.open
                        ml_text = (
                            f" ml_probability={candidate.ml_probability:.2f} "
                            f"ml_score={candidate.ml_score:.1f};"
                            if candidate.ml_probability is not None
                            and candidate.ml_score is not None
                            else ""
                        )
                        entry_reason = (
                            f"Rank {candidate.rank or 1} "
                            f"opportunity_score={combined_score:.1f};{ml_text} "
                            f"{candidate.explanation}"
                        )
                        entry_ml_probability = candidate.ml_probability
                        entry_ml_score = candidate.ml_score
                        self.cash -= execution_candle.open * decision.quantity

                        immediate_price, immediate_reason = (
                            self._intrabar_stop_target(
                                position, execution_candle
                            )
                        )
                        if immediate_reason:
                            trade = self._close_trade(
                                position_symbol,
                                position,
                                timestamp,
                                immediate_price,
                                entry_reason,
                                immediate_reason,
                                holding_bars=0,
                                entry_ml_probability=entry_ml_probability,
                                entry_ml_score=entry_ml_score,
                            )
                            self.cash += (
                                position.entry_price * position.quantity
                                + trade.net_pnl
                            )
                            trades.append(trade)
                            position = None
                            position_symbol = ""
                            entry_reason = ""
                            entry_index = -1
                            last_exit_index = index
                            highest_completed_high = 0.0
                            entry_ml_probability = None
                            entry_ml_score = None
                        else:
                            highest_completed_high = max(
                                highest_completed_high,
                                execution_candle.high,
                            )
                        break

            equity_curve.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "equity": self._mark_to_market(
                        position,
                        position_symbol,
                        timestamp,
                        by_symbol_time,
                    ),
                }
            )

        if position is not None:
            last = self._latest_at_or_before(
                histories[position_symbol], all_times[-1]
            )
            if last is not None:
                holding_bars = max(0, len(all_times) - 1 - entry_index)
                trade = self._close_trade(
                    position_symbol,
                    position,
                    all_times[-1],
                    last.close,
                    entry_reason,
                    "End of portfolio simulation",
                    holding_bars=holding_bars,
                    entry_ml_probability=entry_ml_probability,
                    entry_ml_score=entry_ml_score,
                )
                self.cash += position.entry_price * position.quantity + trade.net_pnl
                trades.append(trade)
                equity_curve.append(
                    {"timestamp": all_times[-1].isoformat(), "equity": self.cash}
                )

        pnls = [trade.net_pnl for trade in trades]
        holding_bars = [trade.holding_bars for trade in trades]
        ending_cash = self.cash
        total_fees = sum(trade.fees for trade in trades)
        total_slippage = sum(trade.slippage_cost for trade in trades)
        total_tax = sum(trade.tax_estimate for trade in trades)
        total_costs = total_fees + total_slippage + total_tax
        gross_activity = sum(abs(trade.gross_pnl) for trade in trades)
        turnover_notional = sum(
            (trade.entry_price + trade.exit_price) * trade.quantity
            for trade in trades
        )
        buy_and_hold = self._equal_weight_buy_and_hold(histories)
        net_return = (ending_cash - self.starting_cash) / self.starting_cash

        return PortfolioResult(
            starting_cash=self.starting_cash,
            ending_cash=ending_cash,
            gross_return=sum(trade.gross_pnl for trade in trades)
            / self.starting_cash,
            net_return=net_return,
            max_drawdown=max_drawdown(
                [float(point["equity"]) for point in equity_curve]
            ),
            win_rate=win_rate(pnls),
            rotations=len(trades),
            average_hold_bars=(
                sum(holding_bars) / len(holding_bars)
                if holding_bars
                else 0.0
            ),
            total_fees=total_fees,
            total_tax=total_tax,
            rejected_opportunities_count=rejected_count,
            trades=trades,
            equity_curve=equity_curve,
            warnings=warnings,
            total_tds_cashflow=sum(
                trade.tds_cashflow for trade in trades
            ),
            cash_return=0.0,
            buy_and_hold_return=buy_and_hold,
            excess_vs_buy_and_hold=net_return - buy_and_hold,
            total_slippage=total_slippage,
            cost_drag_ratio=(
                total_costs / max(gross_activity, 1e-9)
                if trades
                else 0.0
            ),
            turnover=turnover_notional / self.starting_cash,
            trades_per_100_bars=self._trade_rate(
                trades, len(all_times)
            ),
            regime_filtered_opportunities=regime_filtered,
            cooldown_skips=cooldown_skips,
        )

    def _advance_protective_stop(
        self, position: Position, highest_completed_high: float
    ) -> None:
        if highest_completed_high >= position.entry_price * (
            1.0 + self.config.breakeven_trigger_pct
        ):
            trailing = highest_completed_high * (
                1.0 - self.config.trailing_stop_pct
            )
            position.stop_loss = max(
                position.stop_loss,
                position.entry_price,
                trailing,
            )

    def _exit_condition(
        self,
        symbol: str,
        history_before_execution: list[Candle],
        bars_held: int,
    ) -> tuple[bool, str]:
        if bars_held < self.config.min_holding_bars:
            return False, ""
        if len(history_before_execution) < self.scanner_config.min_candles:
            return False, ""

        if self.config.use_regime_filter:
            snapshot = classify_regime(
                history_before_execution,
                self.config.regime_filter,
            )
            if not regime_allows_strategy("momentum", snapshot):
                return True, f"Confirmed regime exit: {snapshot.name}"

        scan = evaluate_symbol(
            symbol,
            Market.CRYPTO,
            history_before_execution,
            self.scanner_config,
            model=self.model,
        )
        if scan.rejected or scan.risk_score >= self.config.danger_risk_score:
            return (
                True,
                f"Confirmed scanner-risk exit: "
                f"{scan.rejection_reason or 'dangerous risk score'}",
            )
        return False, ""

    def _intrabar_stop_target(
        self, position: Position, candle: Candle
    ) -> tuple[float, str]:
        if candle.open <= position.stop_loss:
            return candle.open, "Stop loss gap exit"
        if candle.open >= position.target:
            return candle.open, "Target gap exit"
        stop_hit = candle.low <= position.stop_loss
        target_hit = candle.high >= position.target
        if stop_hit and target_hit:
            if self.config.intrabar_policy == "best_case":
                return position.target, "Target hit"
            return position.stop_loss, "Stop loss hit"
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
            return candle.open, "Max holding period reached"
        if (
            bars_held >= self.config.min_holding_bars
            and exit_streak >= self.config.exit_confirmation_bars
        ):
            return (
                candle.open,
                exit_condition_reason or "Confirmed risk exit",
            )
        return 0.0, ""

    def _close_trade(
        self,
        symbol: str,
        position: Position,
        exit_time: datetime,
        exit_price: float,
        entry_reason: str,
        exit_reason: str,
        *,
        holding_bars: int,
        entry_ml_probability: float | None = None,
        entry_ml_score: float | None = None,
    ) -> PortfolioTrade:
        gross = (exit_price - position.entry_price) * position.quantity
        costs = self.costs.estimate(
            Market.CRYPTO,
            position.entry_price,
            exit_price,
            position.quantity,
        )
        tax_result = self.tax.estimate(
            Market.CRYPTO,
            gross,
            exit_value=exit_price * position.quantity,
        )
        tax = float(tax_result["tax"])
        tds = float(tax_result["tds_cashflow"])
        net = gross - costs["fees"] - costs["slippage"] - tax
        return PortfolioTrade(
            symbol=symbol,
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
            pnl_percent=net
            / max(position.entry_price * position.quantity, 1e-9),
            entry_reason=entry_reason,
            exit_reason=exit_reason,
            entry_ml_probability=entry_ml_probability,
            entry_ml_score=entry_ml_score,
            tds_cashflow=tds,
            holding_bars=holding_bars,
        )

    def _mark_to_market(
        self,
        position: Position | None,
        symbol: str,
        timestamp: datetime,
        by_symbol_time: dict[str, dict[datetime, Candle]],
    ) -> float:
        if position is None:
            return self.cash
        candle = by_symbol_time.get(symbol, {}).get(timestamp)
        price = candle.close if candle else position.entry_price
        return self.cash + position.quantity * price

    @staticmethod
    def _latest_at_or_before(
        candles: list[Candle], timestamp: datetime
    ) -> Candle | None:
        available = [
            candle for candle in candles if candle.timestamp <= timestamp
        ]
        return available[-1] if available else None

    @staticmethod
    def _trade_rate(
        trades: list[PortfolioTrade], bars: int
    ) -> float:
        return len(trades) / max(bars, 1) * 100.0

    @staticmethod
    def _equal_weight_buy_and_hold(
        histories: dict[str, list[Candle]],
    ) -> float:
        returns = []
        for candles in histories.values():
            if len(candles) < 2 or candles[0].open <= 0:
                continue
            returns.append(
                (candles[-1].close - candles[0].open)
                / candles[0].open
            )
        return sum(returns) / len(returns) if returns else 0.0
