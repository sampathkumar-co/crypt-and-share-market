from __future__ import annotations

import json
import math
from dataclasses import asdict, is_dataclass
from datetime import datetime

from tradebot.backtest.ml_comparison import MLComparisonReport
from tradebot.backtest.portfolio_trader import PortfolioResult
from tradebot.backtest.research_gate import ResearchGateReport
from tradebot.backtest.robustness import RobustnessReport
from tradebot.models import BacktestResult, ScanResult, WalkForwardResult

DISCLAIMER = "WARNING: Paper-trading research only. Trading is risky; results are not guaranteed. Not financial or tax advice."


def _default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, float) and not math.isfinite(value):
        return "Infinity" if value > 0 else "-Infinity"
    return str(value)


def backtest_console(result: BacktestResult) -> str:
    return "\n".join(
        [
            DISCLAIMER,
            f"Starting cash: {result.starting_cash:.2f}",
            f"Ending cash: {result.ending_cash:.2f}",
            f"Gross return: {result.gross_return:.2%}",
            f"Net return: {result.net_return:.2%}",
            f"Cash return: {result.cash_return:.2%}",
            f"Buy-and-hold return: {result.buy_and_hold_return:.2%}",
            f"Excess return vs buy-and-hold: {result.excess_return:.2%}",
            f"Win rate: {result.win_rate:.2%}",
            f"Max drawdown: {result.max_drawdown:.2%}",
            f"Sharpe / Sortino / Calmar: {result.sharpe_ratio:.2f} / {result.sortino_ratio:.2f} / {result.calmar_ratio:.2f}",
            f"Profit factor / expectancy: {result.profit_factor:.2f} / {result.expectancy:.2f}",
            f"Exposure: {result.exposure:.2%}",
            f"Average holding bars: {result.average_holding_bars:.2f}",
            f"Trades per 100 bars: {result.trades_per_100_bars:.2f}",
            f"Turnover multiple: {result.turnover:.2f}",
            f"Cost drag ratio: {result.cost_drag_ratio:.2%}",
            f"Regime-filtered entries: {result.regime_rejections}",
            f"Total fees: {result.total_fees:.2f}",
            f"Total slippage: {result.total_slippage:.2f}",
            f"Total estimated tax: {result.total_tax:.2f}",
            f"VDA TDS cash-flow estimate: {result.total_tds_cashflow:.2f}",
            f"Trades: {len(result.trades)}",
            f"Rejected signals: {len(result.rejected_signals)}",
            f"Average win/loss: {result.average_win:.2f}/{result.average_loss:.2f}",
            f"Assumptions: {'; '.join(result.risk_warnings) if result.risk_warnings else '-'}",
        ]
    )


def scan_console(results: list[ScanResult]) -> str:
    header = (
        "rank symbol action opportunity combined ml_prob ml_score risk confidence expected_move% "
        "net_after_cost_tax% rejected rejection_reason explanation"
    )
    lines = [DISCLAIMER, header]
    for result in results:
        lines.append(
            f"{result.rank:>4} {result.symbol:<12} {result.signal.action.value:<4} "
            f"{result.opportunity_score:>6.1f} {(result.combined_opportunity_score if result.combined_opportunity_score is not None else result.opportunity_score):>6.1f} "
            f"{(result.ml_probability if result.ml_probability is not None else 0.0):>6.2f} {(result.ml_score if result.ml_score is not None else 0.0):>6.1f} "
            f"{result.risk_score:>5.1f} {result.confidence:>6.2f} {result.expected_move_percent:>8.2f} "
            f"{result.estimated_net_profit_after_cost_tax:>8.2f} "
            f"{str(result.rejected):<5} {result.rejection_reason or '-':<28} {result.explanation}"
        )
    return "\n".join(lines)


def portfolio_console(result: PortfolioResult) -> str:
    return "\n".join(
        [
            DISCLAIMER,
            f"Starting cash: {result.starting_cash:.2f}",
            f"Ending cash: {result.ending_cash:.2f}",
            f"Gross return: {result.gross_return:.2%}",
            f"Net return after costs/taxes: {result.net_return:.2%}",
            f"Cash return: {result.cash_return:.2%}",
            f"Equal-weight buy-and-hold return: {result.buy_and_hold_return:.2%}",
            f"Excess vs buy-and-hold: {result.excess_vs_buy_and_hold:.2%}",
            f"Max drawdown: {result.max_drawdown:.2%}",
            f"Win rate: {result.win_rate:.2%}",
            f"Rotations/trades: {result.rotations}",
            f"Trades per 100 bars: {result.trades_per_100_bars:.2f}",
            f"Average hold bars: {result.average_hold_bars:.2f}",
            f"Turnover multiple: {result.turnover:.2f}",
            f"Cost drag ratio: {result.cost_drag_ratio:.2%}",
            f"Total fees: {result.total_fees:.2f}",
            f"Total slippage: {result.total_slippage:.2f}",
            f"Total estimated tax: {result.total_tax:.2f}",
            f"Regime-filtered opportunities: {result.regime_filtered_opportunities}",
            f"Cooldown skips: {result.cooldown_skips}",
            f"Rejected opportunities: {result.rejected_opportunities_count}",
            f"Warnings: {'; '.join(result.warnings) if result.warnings else '-'}",
        ]
    )


def ml_comparison_console(result: MLComparisonReport) -> str:
    delta = result.delta_metrics
    return "\n".join(
        [
            DISCLAIMER,
            f"ML comparison verdict: {result.verdict}",
            f"Why: {'; '.join(result.reasons)}",
            f"Baseline ending/net/dd/win/trades: {result.baseline_result.ending_cash:.2f}/{result.baseline_result.net_return:.2%}/{result.baseline_result.max_drawdown:.2%}/{result.baseline_result.win_rate:.2%}/{result.baseline_result.rotations}",
            f"ML ending/net/dd/win/trades: {result.ml_result.ending_cash:.2f}/{result.ml_result.net_return:.2%}/{result.ml_result.max_drawdown:.2%}/{result.ml_result.win_rate:.2%}/{result.ml_result.rotations}",
            f"Delta ending cash: {delta['ending_cash']:.2f}",
            f"Delta net return: {delta['net_return']:.2%}",
            f"Delta max drawdown: {delta['max_drawdown']:.2%}",
            f"Delta win rate: {delta['win_rate']:.2%}",
            f"Delta trades: {delta['trades']:.0f}",
            f"Delta fees/tax: {delta['fees_tax']:.2f}",
            f"Warnings: {'; '.join(result.warnings)}",
            "More paper testing is required; this is not approval for live trading.",
        ]
    )


def robustness_console(result: RobustnessReport) -> str:
    lines = [
        DISCLAIMER,
        f"Robustness status: {result.status}",
        f"Why: {'; '.join(result.reasons)}",
        f"Profitable windows: {result.profitable_windows_percent:.2%}",
        f"Average/median net return: {result.average_net_return:.2%}/{result.median_net_return:.2%}",
        f"Worst window return: {result.worst_window_return:.2%}",
        f"Worst drawdown: {result.worst_drawdown:.2%}",
        f"Consistency score: {result.consistency_score:.2f}",
        f"Crash survival score: {result.crash_survival_score:.2f}",
        f"Tax drag score: {result.tax_drag_score:.2f}",
        f"Overtrading warning: {result.overtrading_warning or '-'}",
        f"Low-trade warning: {result.low_trade_warning or '-'}",
        f"Failing regimes: {', '.join(result.failing_regimes) if result.failing_regimes else '-'}",
        "Best windows:",
    ]
    lines.extend(
        f"  {window.window_name} {window.market_regime} net={window.net_return:.2%} dd={window.max_drawdown:.2%} trades={window.trades}"
        for window in result.best_windows
    )
    lines.append("Worst windows:")
    lines.extend(
        f"  {window.window_name} {window.market_regime} net={window.net_return:.2%} dd={window.max_drawdown:.2%} trades={window.trades}"
        for window in result.worst_windows
    )
    lines.append("Ready only for more paper testing; not approved for live trading.")
    return "\n".join(lines)


def walk_forward_console(result: WalkForwardResult) -> str:
    return (
        f"{DISCLAIMER}\nStability score: {result.stability_score:.2%}\nAccepted: {result.accepted}"
        f"\nReason: {result.reason}\nIndependent unseen windows: {len(result.windows)}"
    )


def research_gate_console(result: ResearchGateReport) -> str:
    lines = [
        DISCLAIMER,
        f"Historical research gate accepted: {result.accepted}",
        f"Eligible for continuous forward paper: {result.eligible_for_continuous_paper}",
        f"Champion strategy: {result.champion_strategy or '-'}",
        f"Passed strategies: {', '.join(result.passed_strategies) if result.passed_strategies else '-'}",
        f"Dataset fingerprint: {result.dataset_fingerprint}",
        f"Why: {'; '.join(result.reasons)}",
    ]
    for strategy in result.strategies:
        lines.append(
            f"{strategy.strategy}: passed={strategy.passed} periods={strategy.independent_periods} "
            f"positive={strategy.positive_unseen_fraction:.0%} "
            f"beat_buy_hold={strategy.beat_buy_and_hold_fraction:.0%} "
            f"avg_unseen={strategy.average_unseen_return:.2%} "
            f"worst={strategy.worst_unseen_return:.2%} "
            f"drawdown={strategy.worst_drawdown:.2%} "
            f"trades/100={strategy.average_trades_per_100_bars:.2f} "
            f"cost_drag={strategy.average_cost_drag_ratio:.2%}"
        )
    if not result.accepted:
        lines.append("Continuous forward paper trading remains blocked.")
    return "\n".join(lines)


def to_json(obj) -> str:
    if isinstance(obj, list):
        payload = [asdict(item) if is_dataclass(item) else item for item in obj]
    elif is_dataclass(obj):
        payload = asdict(obj)
    else:
        payload = obj
    return json.dumps(payload, default=_default, indent=2, allow_nan=False)
