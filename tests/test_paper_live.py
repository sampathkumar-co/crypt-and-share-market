from __future__ import annotations

import json
from datetime import datetime, timedelta

from tradebot.backtest.paper_live import PaperLiveCryptoBot
from tradebot.models import Candle
from tradebot.scanner.crypto_scanner import ScannerConfig


class MockProvider:
    def __init__(self, batches):
        self.batches = batches
        self.calls = 0
        self.order_calls = 0

    def fetch_symbol(self, symbol: str, interval: str = "1m", days: int = 60):
        batch_index = min(self.calls, len(self.batches[symbol]) - 1)
        if symbol == sorted(self.batches)[-1]:
            self.calls += 1
        return self.batches[symbol][batch_index]


def candles(count=35, step=1.0, target_last=False, stop_last=False, volumes=None):
    start = datetime(2025, 1, 1)
    out = []
    volumes = volumes or [5000 + i * 100 for i in range(count)]
    price = 100.0
    for index in range(count):
        open_price = price
        price += step
        high = max(open_price, price) * 1.01
        low = min(open_price, price) * 0.99
        if target_last and index == count - 1:
            high = open_price * 1.08
        if stop_last and index == count - 1:
            low = open_price * 0.94
        out.append(Candle(start + timedelta(minutes=index), open_price, high, low, price, volumes[index]))
    return out


def bot(tmp_path, provider, cash=100000):
    return PaperLiveCryptoBot(
        ["BTCUSDT", "ETHUSDT"],
        "1m",
        cash,
        tmp_path / "state.json",
        provider=provider,
        scanner_config=ScannerConfig(min_candles=30, min_average_volume=100, min_expected_net_percent=0.1),
        lookback_candles=60,
        max_holding_loops=5,
    )


def test_initial_state_creation(tmp_path):
    provider = MockProvider({"BTCUSDT": [candles()], "ETHUSDT": [candles(step=0.1, volumes=[10 for _ in range(35)])]})
    live = bot(tmp_path, provider)
    assert (tmp_path / "state.json").exists()
    assert live.state.cash == 100000


def test_resume_existing_state(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"cash": 1234, "open_position": None, "trade_history": [], "equity_history": [], "last_processed_timestamp": {}, "rejected_opportunities_count": 0, "warnings": [], "errors": [], "loops_completed": 2}))
    provider = MockProvider({"BTCUSDT": [candles()], "ETHUSDT": [candles(step=0.1, volumes=[10 for _ in range(35)])]})
    live = bot(tmp_path, provider)
    assert live.state.cash == 1234
    assert live.state.loops_completed == 2


def test_paper_entry_from_mocked_provider_data(tmp_path):
    provider = MockProvider({"BTCUSDT": [candles(step=1.2)], "ETHUSDT": [candles(step=0.1, volumes=[10 for _ in range(35)])]})
    live = bot(tmp_path, provider)
    live.run(max_loops=1, sleep_seconds=0)
    assert live.state.open_position is not None
    assert live.state.open_position["symbol"] in {"BTCUSDT", "ETHUSDT"}


def test_target_exit_in_paper_live_loop(tmp_path):
    provider = MockProvider({"BTCUSDT": [candles(step=1.2), candles(step=1.2, target_last=True)], "ETHUSDT": [candles(step=0.1, volumes=[10 for _ in range(35)]), candles(step=0.1, volumes=[10 for _ in range(35)])]})
    live = bot(tmp_path, provider)
    live.run(max_loops=2, sleep_seconds=0)
    assert any(trade["exit_reason"] == "Target hit" for trade in live.state.trade_history)


def test_stop_exit_in_paper_live_loop(tmp_path):
    provider = MockProvider({"BTCUSDT": [candles(step=1.2), candles(step=1.2, stop_last=True)], "ETHUSDT": [candles(step=0.1, volumes=[10 for _ in range(35)]), candles(step=0.1, volumes=[10 for _ in range(35)])]})
    live = bot(tmp_path, provider)
    live.run(max_loops=2, sleep_seconds=0)
    assert any(trade["exit_reason"] == "Stop loss hit" for trade in live.state.trade_history)


def test_max_loops_exits_and_no_order_requirement(tmp_path):
    provider = MockProvider({"BTCUSDT": [candles(step=1.2)], "ETHUSDT": [candles(step=0.1, volumes=[10 for _ in range(35)])]})
    live = bot(tmp_path, provider)
    live.run(max_loops=1, sleep_seconds=0)
    assert live.state.loops_completed == 1
    assert provider.order_calls == 0


def test_state_json_shape(tmp_path):
    provider = MockProvider({"BTCUSDT": [candles(step=1.2)], "ETHUSDT": [candles(step=0.1, volumes=[10 for _ in range(35)])]})
    live = bot(tmp_path, provider)
    live.run(max_loops=1, sleep_seconds=0)
    payload = json.loads((tmp_path / "state.json").read_text())
    assert "cash" in payload
    assert "open_position" in payload
    assert "trade_history" in payload
    assert "equity_history" in payload
    assert "last_processed_timestamp" in payload
    assert "warnings" in payload
