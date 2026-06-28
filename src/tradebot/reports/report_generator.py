from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime

from tradebot.backtest.portfolio_trader import PortfolioResult
from tradebot.backtest.robustness import RobustnessReport
from tradebot.models import BacktestResult, ScanResult, WalkForwardResult

DISCLAIMER = "WARNING: Paper-trading research only. Trading is risky; results are not guaranteed. Not financial or tax advice."


def _default(o):
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "value"):
        return o.value
    return str(o)


def backtest_console(result: BacktestResult) -> str:
    return "\n".join([
        DISCLAIMER,
        f"Starting cash: {result.starting_cash:.2f}", f"Ending cash: {result.ending_cash:.2f}",
        f"Gross return: {result.gross_return:.2%}", f"Net return: {result.net_return:.2%}",
        f"Win rate: {result.win_rate:.2%}", f"Max drawdown: {result.max_drawdown:.2%}",
        f"Total fees: {result.total_fees:.2f}", f"Total estimated tax: {result.total_tax:.2f}",
        f"Trades: {len(result.trades)}", f"Rejected signals: {len(result.rejected_signals)}",
        f"Average win/loss: {result.average_win:.2f}/{result.average_loss:.2f}",
    ])


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
    return "\n".join([
        DISCLAIMER,
        f"Starting cash: {result.starting_cash:.2f}",
        f"Ending cash: {result.ending_cash:.2f}",
        f"Gross return: {result.gross_return:.2%}",
        f"Net return after costs/taxes: {result.net_return:.2%}",
        f"Max drawdown: {result.max_drawdown:.2%}",
        f"Win rate: {result.win_rate:.2%}",
        f"Rotations/trades: {result.rotations}",
        f"Average hold bars: {result.average_hold_bars:.2f}",
        f"Total fees: {result.total_fees:.2f}",
        f"Total estimated tax: {result.total_tax:.2f}",
        f"Rejected opportunities: {result.rejected_opportunities_count}",
        f"Warnings: {'; '.join(result.warnings) if result.warnings else '-'}",
    ])


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
    lines.extend(f"  {w.window_name} {w.market_regime} net={w.net_return:.2%} dd={w.max_drawdown:.2%} trades={w.trades}" for w in result.best_windows)
    lines.append("Worst windows:")
    lines.extend(f"  {w.window_name} {w.market_regime} net={w.net_return:.2%} dd={w.max_drawdown:.2%} trades={w.trades}" for w in result.worst_windows)
    lines.append("Ready only for more paper testing; not approved for live trading.")
    return "\n".join(lines)


def walk_forward_console(result: WalkForwardResult) -> str:
    return f"{DISCLAIMER}\nStability score: {result.stability_score:.2%}\nAccepted: {result.accepted}\nReason: {result.reason}\nWindows: {len(result.windows)}"


def to_json(obj) -> str:
    if isinstance(obj, list):
        payload = [asdict(x) if is_dataclass(x) else x for x in obj]
    elif is_dataclass(obj):
        payload = asdict(obj)
    else:
        payload = obj
    return json.dumps(payload, default=_default, indent=2)
