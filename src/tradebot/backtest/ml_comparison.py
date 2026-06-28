from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tradebot.backtest.portfolio_trader import CryptoPortfolioPaperTrader, PortfolioResult
from tradebot.data.csv_loader import load_candles
from tradebot.ml.crypto_signal_model import CryptoSignalModel


@dataclass
class MLComparisonReport:
    baseline_result: PortfolioResult
    ml_result: PortfolioResult
    delta_metrics: dict[str, float]
    verdict: str
    reasons: list[str]
    warnings: list[str]
    paper_testing_only: bool = True


def compare_crypto_ml(folder: str | Path, model_path: str | Path, cash: float = 100000.0) -> MLComparisonReport:
    histories = {path.stem: load_candles(path) for path in sorted(Path(folder).glob("*.csv"))}
    model = CryptoSignalModel.load(model_path)
    baseline = CryptoPortfolioPaperTrader(cash=cash).run(histories)
    ml_result = CryptoPortfolioPaperTrader(cash=cash, model=model).run(histories)
    return compare_results(baseline, ml_result)


def compare_results(baseline: PortfolioResult, ml_result: PortfolioResult) -> MLComparisonReport:
    baseline_worst = min((trade.net_pnl for trade in baseline.trades), default=0.0)
    ml_worst = min((trade.net_pnl for trade in ml_result.trades), default=0.0)
    baseline_best = max((trade.net_pnl for trade in baseline.trades), default=0.0)
    ml_best = max((trade.net_pnl for trade in ml_result.trades), default=0.0)
    baseline_drag = baseline.total_fees + baseline.total_tax
    ml_drag = ml_result.total_fees + ml_result.total_tax
    delta = {
        "ending_cash": ml_result.ending_cash - baseline.ending_cash,
        "net_return": ml_result.net_return - baseline.net_return,
        "max_drawdown": ml_result.max_drawdown - baseline.max_drawdown,
        "win_rate": ml_result.win_rate - baseline.win_rate,
        "trades": float(ml_result.rotations - baseline.rotations),
        "average_hold_bars": ml_result.average_hold_bars - baseline.average_hold_bars,
        "fees_tax": ml_drag - baseline_drag,
        "rejected_opportunities": float(ml_result.rejected_opportunities_count - baseline.rejected_opportunities_count),
        "worst_losing_trade": ml_worst - baseline_worst,
        "best_winning_trade": ml_best - baseline_best,
        "risk_adjusted_return": _risk_adjusted(ml_result) - _risk_adjusted(baseline),
    }
    reasons: list[str] = []
    warnings: list[str] = []
    meaningful_return = delta["net_return"] >= 0.002
    drawdown_ok = delta["max_drawdown"] <= 0.001
    risk_or_win_ok = delta["win_rate"] > 0 or delta["risk_adjusted_return"] > 0
    trade_count_ok = ml_result.rotations >= 2
    drag_ok = delta["fees_tax"] <= max(50.0, baseline_drag * 0.25)

    if meaningful_return: reasons.append("ML improved net return meaningfully.")
    else: reasons.append("ML did not improve net return meaningfully.")
    if drawdown_ok: reasons.append("ML did not materially worsen drawdown.")
    else: reasons.append("ML worsened drawdown.")
    if risk_or_win_ok: reasons.append("ML improved win rate or risk-adjusted return.")
    else: reasons.append("ML did not improve win rate or risk-adjusted return.")
    if not trade_count_ok: warnings.append("ML trade count is dangerously low for conclusions.")
    if not drag_ok: reasons.append("ML increased fee/tax drag too much.")
    if delta["trades"] > max(3.0, baseline.rotations * 0.5): reasons.append("ML may be overtrading.")
    if delta["net_return"] < -0.0005 or delta["max_drawdown"] > 0.01 or not drag_ok:
        verdict = "ML_HURT"
    elif meaningful_return and drawdown_ok and risk_or_win_ok and trade_count_ok and drag_ok:
        verdict = "ML_HELPED"
    else:
        verdict = "ML_NEUTRAL"
    warnings.append("Paper comparison only; ML paper improvement does not guarantee live profit.")
    return MLComparisonReport(baseline, ml_result, delta, verdict, reasons, warnings)


def _risk_adjusted(result: PortfolioResult) -> float:
    return result.net_return / max(result.max_drawdown, 0.01)
