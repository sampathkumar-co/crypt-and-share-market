from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from tradebot.data.forward_market_state import (
    ForwardDataError,
    ForwardMarketStateCollector,
    HYPERLIQUID_INFO,
    canonical_json,
    coinbase_trade_metrics,
    latest_csv_values,
    order_book_metrics,
    sha256_bytes,
)


class FakeClient:
    def __init__(self, *, crossed: bool = False, last_price: str = "100"):
        self.crossed = crossed
        self.last_price = last_price

    @staticmethod
    def _encoded(payload):
        return payload, canonical_json(payload).encode("utf-8")

    def json_request(self, url: str, *, payload=None):
        if url == HYPERLIQUID_INFO and payload == {"type": "metaAndAssetCtxs"}:
            return self._encoded([
                {"universe": [{"name": "BTC"}]},
                [{"markPx": "101", "oraclePx": "100.5", "funding": "0.0001", "openInterest": "10", "dayNtlVlm": "500000", "premium": "0.0002"}],
            ])
        if url == HYPERLIQUID_INFO and payload == {"type": "l2Book", "coin": "BTC"}:
            bid = "101.5" if self.crossed else "100.8"
            return self._encoded({
                "coin": "BTC",
                "time": 1_722_400_000_000,
                "levels": [[{"px": bid, "sz": "2"}], [{"px": "101.2", "sz": "3"}]],
            })
        if url == HYPERLIQUID_INFO and payload == {"type": "recentTrades", "coin": "BTC"}:
            return self._encoded([
                {"px": "101", "sz": "1", "side": "B", "time": 1},
                {"px": "100", "sz": "2", "side": "A", "time": 2},
            ])
        if url.endswith("/ticker"):
            return self._encoded({
                "trade_id": 1,
                "price": self.last_price,
                "size": "0.1",
                "time": "2026-07-30T12:34:00Z",
                "bid": "99.9",
                "ask": "100.1",
                "volume": "1234",
            })
        if "/book?level=2" in url:
            return self._encoded({"sequence": 7, "bids": [["99.9", "2", 1]], "asks": [["100.1", "3", 1]]})
        if "/trades?limit=1000" in url:
            return self._encoded([
                {"price": "100", "size": "1", "side": "sell", "trade_id": 1},
                {"price": "99", "size": "2", "side": "buy", "trade_id": 2},
            ])
        raise AssertionError(f"Unexpected JSON request: {url} {payload}")

    def bytes_request(self, url: str) -> bytes:
        if url.endswith("/usdt.csv"):
            return b"time,CapMrktCurUSD,TxTfrValAdjUSD\n2026-07-29,140000000000,30000000000\n"
        if url.endswith("/usdc.csv"):
            return b"time,CapMrktCurUSD,TxTfrValAdjUSD\n2026-07-29,65000000000,12000000000\n"
        if "VIXCLS" in url:
            return b"observation_date,VIXCLS\n2026-07-29,18.5\n"
        if "DTWEXBGS" in url:
            return b"observation_date,DTWEXBGS\n2026-07-29,121.2\n"
        if "DGS10" in url:
            return b"observation_date,DGS10\n2026-07-29,4.11\n"
        raise AssertionError(f"Unexpected bytes request: {url}")


def test_order_book_metrics_computes_spread_and_imbalance():
    result = order_book_metrics([["100", "2"]], [["101", "1"]])
    assert result.mid == 100.5
    assert result.spread_bps == pytest.approx(99.50248756)
    assert result.imbalance == pytest.approx((200 - 101) / 301)


def test_crossed_book_fails_closed():
    with pytest.raises(ForwardDataError, match="Crossed book"):
        order_book_metrics([["101", "1"]], [["100", "1"]])


def test_coinbase_maker_side_is_inverted_for_taker_flow():
    result = coinbase_trade_metrics([
        {"price": "100", "size": "2", "side": "sell"},
        {"price": "100", "size": "1", "side": "buy"},
    ])
    assert result["taker_buy_notional"] == 200
    assert result["taker_sell_notional"] == 100
    assert result["taker_imbalance"] == pytest.approx(1 / 3)


def test_latest_csv_values_supports_observation_date_and_time():
    fred = latest_csv_values(b"observation_date,VIXCLS\n2026-07-29,18.5\n", ("VIXCLS",))
    metrics = latest_csv_values(b"time,CapMrktCurUSD\n2026-07-29,123\n", ("CapMrktCurUSD",))
    assert fred == {"observation_date": "2026-07-29", "values": {"VIXCLS": 18.5}}
    assert metrics["observation_date"] == "2026-07-29"


def test_collector_writes_portable_hashed_snapshot(tmp_path):
    captured = datetime(2026, 7, 30, 12, 34, 56, tzinfo=timezone.utc)
    result = ForwardMarketStateCollector(FakeClient()).collect(
        tmp_path,
        assets=("BTC",),
        captured_at=captured,
        include_external=True,
    )
    snapshot = result["snapshot"]
    manifest = result["manifest"]
    unhashed = dict(snapshot)
    record_sha = unhashed.pop("record_sha256")
    assert sha256_bytes(canonical_json(unhashed).encode("utf-8")) == record_sha
    assert snapshot["liquidation_events"]["available"] is False
    assert snapshot["assets"]["BTC"]["cross_venue"]["spot_perp_basis_bps"] == pytest.approx(100.0)
    assert snapshot["assets"]["BTC"]["spot_trade_flow"]["taker_buy_notional"] == 100
    assert snapshot["global"]["fred"]["VIXCLS"]["staleness_days"] == 1
    assert all(not entry["raw_path"].startswith("C:") for entry in manifest["sources"])
    assert manifest["normalized_path"].startswith("normalized/")
    assert json.loads((tmp_path / manifest["normalized_path"]).read_text()) == snapshot


def test_collector_is_deterministic_with_fixed_inputs(tmp_path):
    captured = datetime(2026, 7, 30, 12, 34, 56, tzinfo=timezone.utc)
    first = ForwardMarketStateCollector(FakeClient()).collect(
        tmp_path / "a", assets=("BTC",), captured_at=captured, include_external=False
    )
    second = ForwardMarketStateCollector(FakeClient()).collect(
        tmp_path / "b", assets=("BTC",), captured_at=captured, include_external=False
    )
    assert first["snapshot"] == second["snapshot"]
    assert first["manifest"]["record_sha256"] == second["manifest"]["record_sha256"]


def test_existing_snapshot_id_cannot_be_overwritten(tmp_path):
    captured = datetime(2026, 7, 30, 12, 34, 56, tzinfo=timezone.utc)
    collector = ForwardMarketStateCollector(FakeClient())
    collector.collect(tmp_path, assets=("BTC",), captured_at=captured, include_external=False)
    with pytest.raises(ForwardDataError, match="already exists"):
        ForwardMarketStateCollector(FakeClient(last_price="999")).collect(
            tmp_path, assets=("BTC",), captured_at=captured, include_external=False
        )


def test_malformed_book_is_disclosed_and_not_used(tmp_path):
    captured = datetime(2026, 7, 30, 12, 34, 56, tzinfo=timezone.utc)
    result = ForwardMarketStateCollector(FakeClient(crossed=True)).collect(
        tmp_path, assets=("BTC",), captured_at=captured, include_external=False
    )
    assert result["snapshot"]["assets"]["BTC"]["perp_book"]["available"] is False
    assert "Crossed book" in result["snapshot"]["source_errors"]["normalized-BTC-perp-book"]
