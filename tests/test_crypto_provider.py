from datetime import datetime

from tradebot.cli import main
from tradebot.data.crypto_provider import (
    PublicCryptoHistoricalClient,
    normalize_binance_klines,
    save_candles_csv,
)
from tradebot.data.csv_loader import load_candles
from tradebot.models import Candle


def kline(ts_ms: int, open_price: str = "100"):
    return [ts_ms, open_price, "110", "90", "105", "1234", ts_ms + 1, "0", 1, "0", "0", "0"]


def test_normalize_binance_klines_sorts_and_deduplicates_timestamps():
    rows = [kline(2_000, "101"), kline(1_000, "100"), kline(2_000, "102")]
    candles = normalize_binance_klines(rows)
    assert [c.timestamp for c in candles] == [datetime.fromtimestamp(1), datetime.fromtimestamp(2)]
    assert candles[-1].open == 102.0


def test_save_candles_csv_matches_loader_format(tmp_path):
    candles = [
        Candle(datetime(2025, 1, 2), 105, 110, 100, 108, 2000),
        Candle(datetime(2025, 1, 1), 100, 106, 99, 105, 1500),
        Candle(datetime(2025, 1, 3), 108, 112, 107, 111, 2500),
        Candle(datetime(2025, 1, 4), 111, 115, 110, 114, 2600),
        Candle(datetime(2025, 1, 5), 114, 118, 113, 117, 2700),
    ]
    path = save_candles_csv("btcusdt", candles, tmp_path)
    assert path.name == "BTCUSDT.csv"
    loaded = load_candles(path)
    assert len(loaded) == 5
    assert loaded[0].timestamp == datetime(2025, 1, 1)


def test_public_client_uses_mock_response_without_network():
    client = PublicCryptoHistoricalClient()
    client._request_klines = lambda symbol, interval, limit: [  # type: ignore[method-assign]
        kline(1_000),
        kline(2_000),
        kline(3_000),
        kline(4_000),
        kline(5_000),
    ]
    candles = client.fetch_symbol("btcusdt", days=5)
    assert len(candles) == 5
    assert candles[0].close == 105.0


def test_fetch_crypto_cli_argument_parsing_with_mock(monkeypatch, tmp_path, capsys):
    class FakeClient:
        def fetch_symbols_to_csv(self, symbols, interval, days, out_dir):
            from tradebot.data.crypto_provider import FetchResult

            assert symbols == ["BTCUSDT", "ETHUSDT"]
            assert interval == "1d"
            assert days == 5
            assert str(out_dir) == str(tmp_path)
            return [FetchResult("BTCUSDT", tmp_path / "BTCUSDT.csv", 5), FetchResult("ETHUSDT", tmp_path / "ETHUSDT.csv", 5)]

    monkeypatch.setattr("tradebot.cli.PublicCryptoHistoricalClient", FakeClient)
    exit_code = main(["fetch-crypto", "--symbols", "BTCUSDT,ethusdt", "--interval", "1d", "--days", "5", "--out", str(tmp_path)])
    assert exit_code == 0
    assert "SAVED BTCUSDT" in capsys.readouterr().out
