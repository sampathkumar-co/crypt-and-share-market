from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import median

from tradebot.backtest.portfolio_trader import CryptoPortfolioPaperTrader, PortfolioResult
from tradebot.data.csv_loader import load_candles
from tradebot.models import Candle


@dataclass(frozen=True)
class TimeWindow:
    name: str
    start: datetime
    end: datetime


@dataclass
class RobustnessWindowResult:
    window_name: str
    start: datetime
    end: datetime
    market_regime: str
    net_return: float
    gross_return: float
    max_drawdown: float
    win_rate: float
    trades: int
    average_hold_bars: float
    fees: float
    tax_estimate: float
    rejected_opportunities: int
    ending_cash: float


@dataclass
class RobustnessReport:
    status: str
    reasons: list[str]
    profitable_windows_percent: float
    average_net_return: float
    median_net_return: float
    worst_window_return: float
    worst_drawdown: float
    consistency_score: float
    crash_survival_score: float
    tax_drag_score: float
    overtrading_warning: str
    low_trade_warning: str
    best_windows: list[RobustnessWindowResult] = field(default_factory=list)
    worst_windows: list[RobustnessWindowResult] = field(default_factory=list)
    failing_regimes: list[str] = field(default_factory=list)
    windows: list[RobustnessWindowResult] = field(default_factory=list)
    paper_testing_only: bool = True


def load_crypto_histories(folder: str | Path) -> dict[str, list[Candle]]:
    return {path.stem: load_candles(path) for path in sorted(Path(folder).glob("*.csv"))}


def split_time_windows(histories: dict[str, list[Candle]], rolling_sizes: tuple[int, ...] = (30, 90, 180)) -> list[TimeWindow]:
    timestamps = sorted({candle.timestamp for candles in histories.values() for candle in candles})
    if not timestamps:
        return []
    windows = [TimeWindow("full", timestamps[0], timestamps[-1])]
    for size in rolling_sizes:
        if len(timestamps) < size:
            continue
        start_index = 0
        while start_index + size <= len(timestamps):
            start = timestamps[start_index]
            end = timestamps[start_index + size - 1]
            windows.append(TimeWindow(f"rolling_{size}d_{start.date()}_{end.date()}", start, end))
            start_index += size
    return windows


def slice_histories(histories: dict[str, list[Candle]], window: TimeWindow) -> dict[str, list[Candle]]:
    return {
        symbol: [candle for candle in candles if window.start <= candle.timestamp <= window.end]
        for symbol, candles in histories.items()
    }


def classify_market_regime(histories: dict[str, list[Candle]], window: TimeWindow, benchmark_symbol: str = "BTCUSDT") -> str:
    candles = histories.get(benchmark_symbol) or next(iter(histories.values()), [])
    window_candles = [candle for candle in candles if window.start <= candle.timestamp <= window.end]
    if len(window_candles) < 2:
        return "sideways"
    start = window_candles[0].close
    end = window_candles[-1].close
    trend = (end - start) / max(start, 1e-9)
    high = max(c.high for c in window_candles)
    low = min(c.low for c in window_candles)
    volatility = (high - low) / max(end, 1e-9)
    if trend <= -0.12 or volatility >= 0.35:
        return "high_volatility_crash_like" if volatility >= 0.35 else "bear_trending_down"
    if trend >= 0.08:
        return "bull_trending_up"
    if trend <= -0.05:
        return "bear_trending_down"
    return "sideways"


def evaluate_window(histories: dict[str, list[Candle]], window: TimeWindow, cash: float) -> RobustnessWindowResult:
    sliced = slice_histories(histories, window)
    result = CryptoPortfolioPaperTrader(cash=cash).run(sliced)
    return _window_result(window, classify_market_regime(histories, window), result)


def evaluate_robustness(folder: str | Path, cash: float = 100000.0) -> RobustnessReport:
    histories = load_crypto_histories(folder)
    windows = split_time_windows(histories)
    results = [evaluate_window(histories, window, cash) for window in windows]
    return score_robustness(results)


def score_robustness(windows: list[RobustnessWindowResult]) -> RobustnessReport:
    if not windows:
        return RobustnessReport(
            status="FAIL",
            reasons=["No windows could be evaluated."],
            profitable_windows_percent=0.0,
            average_net_return=0.0,
            median_net_return=0.0,
            worst_window_return=0.0,
            worst_drawdown=0.0,
            consistency_score=0.0,
            crash_survival_score=0.0,
            tax_drag_score=0.0,
            overtrading_warning="No trades evaluated.",
            low_trade_warning="No trades evaluated.",
        )

    returns = [window.net_return for window in windows]
    profitable_percent = sum(1 for value in returns if value > 0) / len(returns)
    average_return = sum(returns) / len(returns)
    median_return = median(returns)
    worst_return = min(returns)
    worst_drawdown = max(window.max_drawdown for window in windows)
    total_trades = sum(window.trades for window in windows)
    total_gross = sum(max(0.0, window.gross_return) for window in windows)
    total_tax_fees_drag = sum(window.fees + window.tax_estimate for window in windows)
    total_cash_basis = max(sum(abs(window.gross_return) for window in windows), 1e-9)
    tax_drag_score = max(0.0, 1.0 - min(1.0, total_tax_fees_drag / max(total_gross * 100000.0, 1e-9))) if total_gross else 0.0
    consistency_score = max(0.0, min(1.0, profitable_percent * 0.6 + max(0.0, average_return - worst_drawdown) * 4.0))
    crash_windows = [window for window in windows if window.market_regime in {"bear_trending_down", "high_volatility_crash_like"}]
    crash_survival_score = (
        sum(1 for window in crash_windows if window.net_return > -0.03 and window.max_drawdown < 0.15) / len(crash_windows)
        if crash_windows
        else 0.5
    )

    reasons: list[str] = []
    if profitable_percent < 0.55:
        reasons.append("Profitable windows below 55% threshold.")
    if worst_drawdown > 0.25:
        reasons.append("Worst drawdown is too high.")
    if average_return > 0 and median_return < 0:
        reasons.append("Average return is positive but median return is negative, suggesting unstable outliers.")
    if total_trades < max(3, len(windows)):
        reasons.append("Too few trades for robust evidence.")
    if tax_drag_score < 0.55:
        reasons.append("Fees/taxes destroy too much gross profit.")
    bull_returns = [window.net_return for window in windows if window.market_regime == "bull_trending_up"]
    non_bull_returns = [window.net_return for window in windows if window.market_regime != "bull_trending_up"]
    if bull_returns and non_bull_returns and sum(1 for value in bull_returns if value > 0) and not any(value > 0 for value in non_bull_returns):
        reasons.append("Strategy appears to work only in bull markets.")
    failed_stress = [window.market_regime for window in crash_windows if window.net_return < -0.05 or window.max_drawdown > 0.20]
    if failed_stress:
        reasons.append("Strategy fails badly in bear/high-volatility windows.")

    average_trades_per_window = total_trades / len(windows)
    overtrading_warning = "High turnover may increase fees/taxes." if average_trades_per_window > 8 else ""
    low_trade_warning = "Too few trades to trust robustness score." if average_trades_per_window < 1 else ""
    if low_trade_warning and low_trade_warning not in reasons:
        reasons.append(low_trade_warning)

    if not reasons and consistency_score >= 0.65 and crash_survival_score >= 0.60:
        status = "PASS"
        reasons = ["Robustness checks passed for continued paper testing; not approved for live trading."]
    elif profitable_percent >= 0.45 and worst_drawdown <= 0.30:
        status = "WATCH"
        if not reasons:
            reasons = ["Mixed robustness metrics; continue paper testing."]
    else:
        status = "FAIL"

    sorted_windows = sorted(windows, key=lambda window: window.net_return, reverse=True)
    failing_regimes = sorted({window.market_regime for window in windows if window.net_return < 0})
    return RobustnessReport(
        status=status,
        reasons=reasons,
        profitable_windows_percent=profitable_percent,
        average_net_return=average_return,
        median_net_return=median_return,
        worst_window_return=worst_return,
        worst_drawdown=worst_drawdown,
        consistency_score=consistency_score,
        crash_survival_score=crash_survival_score,
        tax_drag_score=tax_drag_score,
        overtrading_warning=overtrading_warning,
        low_trade_warning=low_trade_warning,
        best_windows=sorted_windows[:3],
        worst_windows=list(reversed(sorted_windows[-3:])),
        failing_regimes=failing_regimes,
        windows=windows,
    )


def _window_result(window: TimeWindow, market_regime: str, result: PortfolioResult) -> RobustnessWindowResult:
    return RobustnessWindowResult(
        window_name=window.name,
        start=window.start,
        end=window.end,
        market_regime=market_regime,
        net_return=result.net_return,
        gross_return=result.gross_return,
        max_drawdown=result.max_drawdown,
        win_rate=result.win_rate,
        trades=result.rotations,
        average_hold_bars=result.average_hold_bars,
        fees=result.total_fees,
        tax_estimate=result.total_tax,
        rejected_opportunities=result.rejected_opportunities_count,
        ending_cash=result.ending_cash,
    )
