from __future__ import annotations

import json
from datetime import datetime, timedelta

from tradebot.backtest.robustness import (
    RobustnessWindowResult,
    TimeWindow,
    classify_market_regime,
    score_robustness,
    split_time_windows,
)
from tradebot.models import Candle
from tradebot.reports.report_generator import to_json


def candles(closes: list[float]) -> list[Candle]:
    start = datetime(2025, 1, 1)
    out = []
    for i, close in enumerate(closes):
        high = close * 1.02
        low = close * 0.98
        out.append(Candle(start + timedelta(days=i), close, high, low, close, 5000))
    return out


def window_result(name: str, regime: str, net: float, gross: float | None = None, dd: float = 0.05, trades: int = 3, fees: float = 10, tax: float = 5) -> RobustnessWindowResult:
    return RobustnessWindowResult(
        window_name=name,
        start=datetime(2025, 1, 1),
        end=datetime(2025, 1, 30),
        market_regime=regime,
        net_return=net,
        gross_return=net if gross is None else gross,
        max_drawdown=dd,
        win_rate=0.5,
        trades=trades,
        average_hold_bars=4,
        fees=fees,
        tax_estimate=tax,
        rejected_opportunities=1,
        ending_cash=100000 * (1 + net),
    )


def test_time_window_splitting():
    histories = {"BTCUSDT": candles([100 + i for i in range(95)])}
    windows = split_time_windows(histories, rolling_sizes=(30, 90, 180))
    assert windows[0].name == "full"
    assert sum(1 for window in windows if window.name.startswith("rolling_30d")) == 3
    assert sum(1 for window in windows if window.name.startswith("rolling_90d")) == 1


def test_market_regime_classification():
    bull = {"BTCUSDT": candles([100 + i for i in range(30)])}
    bear = {"BTCUSDT": candles([130 - i for i in range(30)])}
    sideways = {"BTCUSDT": candles([100 + (i % 2) for i in range(30)])}
    wild = {"BTCUSDT": candles([100 if i % 2 == 0 else 150 for i in range(30)])}
    window = TimeWindow("test", datetime(2025, 1, 1), datetime(2025, 1, 30))
    assert classify_market_regime(bull, window) == "bull_trending_up"
    assert classify_market_regime(bear, window) in {"bear_trending_down", "high_volatility_crash_like"}
    assert classify_market_regime(sideways, window) == "sideways"
    assert classify_market_regime(wild, window) == "high_volatility_crash_like"


def test_robustness_scoring_pass_watch_or_fail():
    report = score_robustness([
        window_result("w1", "bull_trending_up", 0.04),
        window_result("w2", "sideways", 0.02),
        window_result("w3", "bear_trending_down", 0.01),
    ])
    assert report.status in {"PASS", "WATCH", "FAIL"}
    assert report.profitable_windows_percent == 1.0
    assert report.average_net_return > 0


def test_rejection_when_strategy_only_works_in_bull_windows():
    report = score_robustness([
        window_result("bull", "bull_trending_up", 0.08),
        window_result("sideways", "sideways", -0.01),
        window_result("bear", "bear_trending_down", -0.04),
    ])
    assert report.status in {"WATCH", "FAIL"}
    assert any("only in bull markets" in reason for reason in report.reasons)


def test_rejection_when_taxes_and_fees_destroy_profits():
    report = score_robustness([
        window_result("w1", "bull_trending_up", 0.001, gross=0.08, fees=3000, tax=3000),
        window_result("w2", "sideways", 0.001, gross=0.07, fees=3000, tax=3000),
        window_result("w3", "bear_trending_down", 0.001, gross=0.06, fees=3000, tax=3000),
    ])
    assert report.status in {"WATCH", "FAIL"}
    assert any("Fees/taxes" in reason for reason in report.reasons)


def test_robustness_json_report_shape():
    report = score_robustness([window_result("w1", "sideways", 0.01)])
    payload = json.loads(to_json(report))
    assert "status" in payload
    assert "windows" in payload
    assert "best_windows" in payload
    assert payload["paper_testing_only"] is True
