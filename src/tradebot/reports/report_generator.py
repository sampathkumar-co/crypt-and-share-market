from __future__ import annotations
import json
from dataclasses import asdict
from datetime import datetime
from tradebot.models import BacktestResult, ScanResult, WalkForwardResult

DISCLAIMER = "WARNING: Paper-trading research only. Trading is risky; results are not guaranteed. Not financial or tax advice."

def _default(o):
    if isinstance(o, datetime): return o.isoformat()
    if hasattr(o, "value"): return o.value
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
    lines=[DISCLAIMER, "symbol action score rank reason"]
    for r in results[:10]: lines.append(f"{r.symbol} {r.signal.action.value} {r.signal.score:.2f} {r.rank_score:.2f} {r.signal.reason}")
    return "\n".join(lines)

def walk_forward_console(result: WalkForwardResult) -> str:
    return f"{DISCLAIMER}\nStability score: {result.stability_score:.2%}\nAccepted: {result.accepted}\nReason: {result.reason}\nWindows: {len(result.windows)}"

def to_json(obj) -> str:
    return json.dumps(asdict(obj) if not isinstance(obj, list) else [asdict(x) for x in obj], default=_default, indent=2)
