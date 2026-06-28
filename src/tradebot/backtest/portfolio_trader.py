from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tradebot.backtest.metrics import max_drawdown, win_rate
from tradebot.data.csv_loader import load_candles
from tradebot.models import Candle, Market, Position, Signal, Action
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


@dataclass(frozen=True)
class PortfolioConfig:
    max_holding_bars: int = 10
    scanner_top: int = 20
    min_symbols: int = 2
    min_candles_per_symbol: int = 30
    danger_risk_score: float = 85.0


class CryptoPortfolioPaperTrader:
    """Paper-only one-position crypto rotation simulator; never places real orders."""

    def __init__(self, cash: float = 100000.0, config: PortfolioConfig | None = None, scanner_config: ScannerConfig | None = None):
        self.starting_cash = cash
        self.cash = cash
        self.config = config or PortfolioConfig()
        self.scanner_config = scanner_config or ScannerConfig()
        self.risk = RiskManager()
        self.costs = CostEngine()
        self.tax = TaxEngine()

    def run_folder(self, folder: str | Path) -> PortfolioResult:
        histories = {path.stem: load_candles(path) for path in sorted(Path(folder).glob("*.csv"))}
        return self.run(histories)

    def run(self, histories: dict[str, list[Candle]]) -> PortfolioResult:
        warnings: list[str] = []
        if len(histories) < self.config.min_symbols:
            warnings.append("Too few symbols for diversified rotation research.")
        short_symbols = [symbol for symbol, candles in histories.items() if len(candles) < self.config.min_candles_per_symbol]
        if short_symbols:
            warnings.append(f"Too few candles for symbols: {', '.join(short_symbols)}")

        all_times = sorted({candle.timestamp for candles in histories.values() for candle in candles})
        by_symbol_time = {symbol: {c.timestamp: c for c in candles} for symbol, candles in histories.items()}
        position: Position | None = None
        position_symbol = ""
        entry_reason = ""
        entry_index = 0
        entry_cash_basis = 0.0
        trades: list[PortfolioTrade] = []
        rejected_count = 0
        equity_curve: list[dict[str, float | str]] = []
        day_start_equity = self.cash
        current_day = None
        halted_for_day = False

        for index, timestamp in enumerate(all_times):
            if current_day != timestamp.date():
                current_day = timestamp.date()
                day_start_equity = self._mark_to_market(position, position_symbol, timestamp, by_symbol_time)
                halted_for_day = False

            if position:
                candle = by_symbol_time.get(position_symbol, {}).get(timestamp)
                if candle:
                    exit_price, exit_reason = self._exit_decision(position, position_symbol, candle, histories[position_symbol], timestamp, index, entry_index)
                    if exit_reason:
                        trade = self._close_trade(position_symbol, position, timestamp, exit_price, entry_reason, exit_reason)
                        self.cash += position.entry_price * position.quantity + trade.net_pnl
                        trades.append(trade)
                        position = None
                        position_symbol = ""
                        entry_reason = ""
                        entry_cash_basis = 0.0
                        if trade.net_pnl < 0 and abs(trade.net_pnl) >= day_start_equity * self.risk.config.max_daily_loss:
                            halted_for_day = True

            equity = self._mark_to_market(position, position_symbol, timestamp, by_symbol_time)
            if equity <= day_start_equity * (1 - self.risk.config.max_daily_loss):
                halted_for_day = True

            if not position and not halted_for_day:
                candidates = []
                for symbol, candles in histories.items():
                    available = [candle for candle in candles if candle.timestamp <= timestamp]
                    if len(available) < self.scanner_config.min_candles:
                        continue
                    scan = evaluate_symbol(symbol, Market.CRYPTO, available, self.scanner_config)
                    if scan.rejected:
                        rejected_count += 1
                        continue
                    candidates.append(scan)
                candidates.sort(key=lambda result: result.opportunity_score, reverse=True)
                for candidate in candidates[: self.config.scanner_top]:
                    latest = self._latest_at_or_before(histories[candidate.symbol], timestamp)
                    if latest is None:
                        continue
                    risk_signal = Signal(Action.BUY, candidate.opportunity_score / 100.0, candidate.explanation, candidate.confidence, candidate.risk_score / 100.0)
                    decision = self.risk.evaluate(Market.CRYPTO, self.cash, candidate.symbol, risk_signal, latest, daily_loss=equity - day_start_equity)
                    if not decision.approved:
                        rejected_count += 1
                        continue
                    position = Position(candidate.symbol, decision.quantity, latest.close, decision.stop_loss, decision.target, timestamp)
                    position_symbol = candidate.symbol
                    entry_index = index
                    entry_reason = f"Rank {candidate.rank or 1} opportunity_score={candidate.opportunity_score:.1f}; {candidate.explanation}"
                    entry_cash_basis = latest.close * decision.quantity
                    self.cash -= entry_cash_basis
                    break

            equity_curve.append({"timestamp": timestamp.isoformat(), "equity": self._mark_to_market(position, position_symbol, timestamp, by_symbol_time)})

        if position:
            last = self._latest_at_or_before(histories[position_symbol], all_times[-1])
            if last:
                trade = self._close_trade(position_symbol, position, all_times[-1], last.close, entry_reason, "End of portfolio simulation")
                self.cash += position.entry_price * position.quantity + trade.net_pnl
                trades.append(trade)
                equity_curve.append({"timestamp": all_times[-1].isoformat(), "equity": self.cash})

        pnls = [trade.net_pnl for trade in trades]
        hold_bars = [max(0, _bars_between(all_times, trade.entry_time, trade.exit_time)) for trade in trades]
        ending_cash = self.cash
        return PortfolioResult(
            starting_cash=self.starting_cash,
            ending_cash=ending_cash,
            gross_return=sum(trade.gross_pnl for trade in trades) / self.starting_cash,
            net_return=(ending_cash - self.starting_cash) / self.starting_cash,
            max_drawdown=max_drawdown([float(point["equity"]) for point in equity_curve]),
            win_rate=win_rate(pnls),
            rotations=len(trades),
            average_hold_bars=sum(hold_bars) / len(hold_bars) if hold_bars else 0.0,
            total_fees=sum(trade.fees for trade in trades),
            total_tax=sum(trade.tax_estimate for trade in trades),
            rejected_opportunities_count=rejected_count,
            trades=trades,
            equity_curve=equity_curve,
            warnings=warnings,
        )

    def _exit_decision(self, position: Position, symbol: str, candle: Candle, candles: list[Candle], timestamp: datetime, index: int, entry_index: int) -> tuple[float, str]:
        if candle.low <= position.stop_loss:
            return position.stop_loss, "Stop loss hit"
        if candle.high >= position.target:
            return position.target, "Target hit"
        if index - entry_index >= self.config.max_holding_bars:
            return candle.close, "Max holding period reached"
        available = [item for item in candles if item.timestamp <= timestamp]
        if len(available) >= self.scanner_config.min_candles:
            scan = evaluate_symbol(symbol, Market.CRYPTO, available, self.scanner_config)
            if scan.rejected or scan.risk_score >= self.config.danger_risk_score:
                return candle.close, f"Scanner risk exit: {scan.rejection_reason or 'dangerous risk score'}"
        return 0.0, ""

    def _close_trade(self, symbol: str, position: Position, exit_time: datetime, exit_price: float, entry_reason: str, exit_reason: str) -> PortfolioTrade:
        gross = (exit_price - position.entry_price) * position.quantity
        costs = self.costs.estimate(Market.CRYPTO, position.entry_price, exit_price, position.quantity)
        tax = self.tax.estimate(Market.CRYPTO, gross)["tax"]
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
            pnl_percent=net / max(position.entry_price * position.quantity, 1e-9),
            entry_reason=entry_reason,
            exit_reason=exit_reason,
        )

    def _mark_to_market(self, position: Position | None, symbol: str, timestamp: datetime, by_symbol_time: dict[str, dict[datetime, Candle]]) -> float:
        if not position:
            return self.cash
        candle = by_symbol_time.get(symbol, {}).get(timestamp)
        price = candle.close if candle else position.entry_price
        return self.cash + position.quantity * price

    @staticmethod
    def _latest_at_or_before(candles: list[Candle], timestamp: datetime) -> Candle | None:
        available = [candle for candle in candles if candle.timestamp <= timestamp]
        return available[-1] if available else None


def _bars_between(times: list[datetime], start: datetime, end: datetime) -> int:
    try:
        return times.index(end) - times.index(start)
    except ValueError:
        return 0
