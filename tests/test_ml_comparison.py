from __future__ import annotations

import json
from datetime import datetime

from tradebot.backtest.ml_comparison import compare_results
from tradebot.backtest.portfolio_trader import PortfolioResult, PortfolioTrade
from tradebot.ml.crypto_signal_model import CryptoSignalModel, train_model, build_samples_from_candles
from tradebot.models import Candle
from tradebot.reports.report_generator import to_json


def candles_from_closes(closes: list[float], volumes: list[float] | None = None, target_bar: int | None = None) -> list[Candle]:
    from datetime import timedelta
    start = datetime(2025, 1, 1)
    volumes = volumes or [5000 + i * 100 for i in range(len(closes))]
    out = []
    for index, close in enumerate(closes):
        open_price = closes[index - 1] if index else close
        high = max(open_price, close) * 1.01
        low = min(open_price, close) * 0.99
        if target_bar is not None and index == target_bar:
            high = max(high, open_price * 1.06)
        out.append(Candle(start + timedelta(days=index), open_price, high, low, close, volumes[index]))
    return out


def trend(start: float, step: float, count: int = 45) -> list[float]:
    return [start + step * i for i in range(count)]


def make_trader():
    from tradebot.backtest.portfolio_trader import CryptoPortfolioPaperTrader, PortfolioConfig
    from tradebot.scanner.crypto_scanner import ScannerConfig
    return CryptoPortfolioPaperTrader(config=PortfolioConfig(min_symbols=1), scanner_config=ScannerConfig(min_candles=30, min_average_volume=100, min_expected_net_percent=0.1))


def result(net: float, dd: float, win: float, trades: int, fees: float = 10, tax: float = 5) -> PortfolioResult:
    trade_list = [
        PortfolioTrade("BTC", datetime(2025, 1, 1), datetime(2025, 1, 2), 100, 104, 1, 4, 1, 1, 1, 1, 0.01, "entry", "exit")
        for _ in range(trades)
    ]
    return PortfolioResult(100000, 100000 * (1 + net), net, net, dd, win, trades, 2, fees, tax, 0, trade_list, [], [])


def test_portfolio_accepts_model_option_and_trade_has_ml_score():
    histories = {"BTCUSDT": candles_from_closes(trend(100, 1.4), target_bar=30), "ALT": candles_from_closes(trend(50, 0.2), [10 for _ in range(45)])}
    samples = build_samples_from_candles("BTCUSDT", histories["BTCUSDT"])
    model = train_model(samples)
    bot = make_trader()
    bot.model = model
    portfolio = bot.run(histories)
    assert portfolio.trades
    assert portfolio.trades[0].entry_ml_score is not None
    assert "ml_probability" in portfolio.trades[0].entry_reason


def test_comparison_verdict_ml_helped():
    report = compare_results(result(0.01, 0.05, 0.5, 3), result(0.02, 0.04, 0.6, 3, fees=11, tax=6))
    assert report.verdict == "ML_HELPED"


def test_comparison_verdict_ml_hurt():
    report = compare_results(result(0.02, 0.04, 0.6, 3), result(0.005, 0.08, 0.4, 8, fees=500, tax=500))
    assert report.verdict == "ML_HURT"


def test_comparison_json_report_shape():
    report = compare_results(result(0.01, 0.05, 0.5, 3), result(0.011, 0.05, 0.5, 3))
    payload = json.loads(to_json(report))
    assert "baseline_result" in payload
    assert "ml_result" in payload
    assert "delta_metrics" in payload
    assert "verdict" in payload


def test_robustness_accepts_model_if_implemented():
    from tradebot.backtest.robustness import TimeWindow, evaluate_window

    histories = {"BTCUSDT": candles_from_closes(trend(100, 1.4), target_bar=30), "ALT": candles_from_closes(trend(50, 0.2), [10 for _ in range(45)])}
    model = train_model(build_samples_from_candles("BTCUSDT", histories["BTCUSDT"]))
    window = TimeWindow("full", histories["BTCUSDT"][0].timestamp, histories["BTCUSDT"][-1].timestamp)
    row = evaluate_window(histories, window, 100000, model=model)
    assert row.trades >= 0
