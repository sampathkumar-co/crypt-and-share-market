from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tradebot.backtest.paper_trader import BacktestConfig, PaperTrader
from tradebot.data.csv_loader import CSVValidationError, audit_candles, load_candles
from tradebot.models import Action, Candle, Market, Signal
from tradebot.reports.report_generator import to_json
from tradebot.risk.tax_engine import TaxEngine
from tradebot.strategies.base import Strategy


class BuyAfterJump(Strategy):
    def generate_signal(self, candles: list[Candle]) -> Signal:
        should_buy = bool(candles and candles[-1].close >= 150)
        return Signal(Action.BUY if should_buy else Action.HOLD, 0.9, "jump", 0.9, 0.2)


class AlwaysBuy(Strategy):
    def generate_signal(self, candles: list[Candle]) -> Signal:
        return Signal(Action.BUY, 0.9, "always", 0.9, 0.2)


def candle(index: int, open_price: float, high: float, low: float, close: float) -> Candle:
    return Candle(datetime(2025, 1, 1) + timedelta(days=index), open_price, high, low, close, 10_000)


def test_backtest_signal_uses_completed_bar_and_fills_next_open():
    candles = [candle(index, 100, 101, 99, 100) for index in range(10)]
    candles.append(candle(10, 100, 205, 99, 200))
    candles.append(candle(11, 300, 305, 295, 300))

    result = PaperTrader(Market.CRYPTO, BuyAfterJump()).run("TEST", candles)

    assert result.trades
    assert result.trades[0].entry_price == 300
    assert "next bar open" in " ".join(result.risk_warnings).lower()


def test_worst_case_intrabar_policy_chooses_stop_when_both_hit():
    candles = [candle(index, 100, 101, 99, 100) for index in range(10)]
    candles.append(candle(10, 100, 101, 99, 100))
    candles.append(candle(11, 100, 105, 97, 100))

    result = PaperTrader(
        Market.CRYPTO,
        AlwaysBuy(),
        config=BacktestConfig(intrabar_policy="worst_case"),
    ).run("TEST", candles)

    assert result.trades[0].exit_price == pytest.approx(98.0)
    assert "worst-case" in result.trades[0].reason


def test_reusing_trader_resets_cash_and_produces_same_result():
    candles = [candle(index, 100, 105, 99, 104) for index in range(14)]
    trader = PaperTrader(Market.CRYPTO, AlwaysBuy())

    first = trader.run("TEST", candles)
    second = trader.run("TEST", candles)

    assert first.ending_cash == pytest.approx(second.ending_cash)
    assert first.net_return == pytest.approx(second.net_return)


def test_tax_engine_uses_current_equity_rates_and_consideration_based_vda_tds():
    engine = TaxEngine()

    assert engine.estimate(Market.EQUITY, 1_000, holding_days=10)["tax"] == pytest.approx(200)
    assert engine.estimate(Market.EQUITY, 1_000, holding_days=366)["tax"] == pytest.approx(125)
    losing_crypto = engine.estimate(Market.CRYPTO, -500, exit_value=10_000)
    assert losing_crypto["tax"] == 0
    assert losing_crypto["tds_cashflow"] == pytest.approx(100)


def test_csv_loader_rejects_duplicate_timestamps(tmp_path):
    path = tmp_path / "duplicate.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2025-01-01T00:00:00,100,101,99,100,1000\n"
        "2025-01-02T00:00:00,100,101,99,100,1000\n"
        "2025-01-03T00:00:00,100,101,99,100,1000\n"
        "2025-01-03T00:00:00,100,101,99,100,1000\n"
        "2025-01-05T00:00:00,100,101,99,100,1000\n",
        encoding="utf-8",
    )

    with pytest.raises(CSVValidationError, match="Duplicate timestamp"):
        load_candles(path)


def test_data_audit_and_result_json_are_serializable():
    candles = [candle(index, 100 + index, 102 + index, 99 + index, 101 + index) for index in range(12)]
    audit = audit_candles(candles)
    result = PaperTrader(Market.CRYPTO, AlwaysBuy()).run("TEST", candles)

    assert audit["candles"] == 12
    assert audit["typical_interval_seconds"] == 86_400
    assert '"profit_factor"' in to_json(result)
