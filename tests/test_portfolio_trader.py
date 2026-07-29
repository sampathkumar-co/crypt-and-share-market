from __future__ import annotations

import json
from datetime import datetime, timedelta

from tradebot.backtest.portfolio_trader import CryptoPortfolioPaperTrader, PortfolioConfig
from tradebot.models import Candle
from tradebot.reports.report_generator import to_json
from tradebot.risk.risk_manager import RiskConfig
from tradebot.scanner.crypto_scanner import ScannerConfig


def candles_from_closes(closes: list[float], volumes: list[float] | None = None, target_bar: int | None = None, stop_bar: int | None = None) -> list[Candle]:
    start = datetime(2025, 1, 1)
    volumes = volumes or [5000 + i * 100 for i in range(len(closes))]
    candles = []
    for index, close in enumerate(closes):
        open_price = closes[index - 1] if index else close
        high = max(open_price, close) * 1.01
        low = min(open_price, close) * 0.99
        if target_bar is not None and index == target_bar:
            high = max(high, open_price * 1.06)
        if stop_bar is not None and index == stop_bar:
            low = min(low, open_price * 0.96)
        candles.append(Candle(start + timedelta(days=index), open_price, high, low, close, volumes[index]))
    return candles


def trend(start: float, step: float, count: int = 45) -> list[float]:
    return [start + step * i for i in range(count)]


def trader(**kwargs) -> CryptoPortfolioPaperTrader:
    bot = CryptoPortfolioPaperTrader(
        cash=kwargs.pop("cash", 100000),
        config=PortfolioConfig(max_holding_bars=kwargs.pop("max_holding_bars", 8), scanner_top=20, min_symbols=1),
        scanner_config=ScannerConfig(min_candles=30, min_average_volume=100, min_expected_net_percent=0.1),
    )
    if "max_daily_loss" in kwargs:
        bot.risk.config = RiskConfig(max_daily_loss=kwargs["max_daily_loss"])
    return bot


def test_portfolio_enters_highest_ranked_accepted_coin():
    histories = {
        "STRONG": candles_from_closes(trend(100, 1.4)),
        "WEAK": candles_from_closes([120 - 0.2 * i for i in range(45)], [2000 for _ in range(45)]),
    }
    result = trader(max_holding_bars=3).run(histories)
    assert result.trades
    assert result.trades[0].symbol == "STRONG"
    assert "opportunity_score" in result.trades[0].entry_reason


def test_portfolio_exits_on_target():
    histories = {
        "TARGET": candles_from_closes(trend(100, 1.2), target_bar=30),
        "ALT": candles_from_closes(trend(50, 0.2), [10 for _ in range(45)]),
    }
    result = trader().run(histories)
    assert any(trade.exit_reason == "Target hit" for trade in result.trades)


def test_portfolio_exits_on_stop_loss():
    histories = {
        "STOP": candles_from_closes(trend(100, 1.2), stop_bar=30),
        "ALT": candles_from_closes(trend(50, 0.2), [10 for _ in range(45)]),
    }
    result = trader().run(histories)
    assert any(trade.exit_reason == "Stop loss hit" for trade in result.trades)


def test_portfolio_rotates_into_another_coin_after_exit():
    histories = {
        "FIRST": candles_from_closes(trend(100, 1.4), target_bar=30),
        "SECOND": candles_from_closes(trend(80, 0.2)[:32] + trend(88, 1.5, 13)),
    }
    result = trader(max_holding_bars=3).run(histories)
    symbols = [trade.symbol for trade in result.trades]
    assert len(symbols) >= 2
    assert len(set(symbols)) >= 2


def test_portfolio_respects_max_position_size():
    histories = {"BIG": candles_from_closes(trend(100, 1.4), target_bar=30), "ALT": candles_from_closes(trend(50, 0.2))}
    result = trader(cash=100000).run(histories)
    first = result.trades[0]
    assert first.entry_price * first.quantity <= 100000 * 0.20 + 1e-6


def test_portfolio_stops_after_daily_loss_limit():
    histories = {
        "LOSS": candles_from_closes(trend(100, 1.4), stop_bar=30),
        "ALT": candles_from_closes(trend(70, 0.2), [10 for _ in range(45)]),
    }
    result = trader(cash=1000, max_daily_loss=0.001).run(histories)
    assert result.trades
    assert all(trade.entry_time.date() != result.trades[0].exit_time.date() for trade in result.trades[1:])


def test_portfolio_json_report_shape():
    histories = {"JSON": candles_from_closes(trend(100, 1.4), target_bar=30), "ALT": candles_from_closes(trend(50, 0.2))}
    result = trader().run(histories)
    payload = json.loads(to_json(result))
    assert "starting_cash" in payload
    assert "ending_cash" in payload
    assert "trades" in payload
    assert "equity_curve" in payload
    assert "rejected_opportunities_count" in payload
