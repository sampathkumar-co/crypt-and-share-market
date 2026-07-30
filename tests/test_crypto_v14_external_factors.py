from __future__ import annotations

from datetime import date
from urllib.error import HTTPError

from tradebot.data import crypto_v14_external_factors as factors


def test_v14_funding_symbol_mapping_is_frozen() -> None:
    assert factors.V14_HYPERLIQUID_COINS == {
        "AVAXUSDT": "AVAX",
        "DOTUSDT": "DOT",
        "NEARUSDT": "NEAR",
        "FILUSDT": "FIL",
        "ICPUSDT": "ICP",
        "OPUSDT": "OP",
        "ARBUSDT": "ARB",
        "SUIUSDT": "SUI",
    }


def test_429_retry_delay_honours_retry_after() -> None:
    error = HTTPError("https://example.test", 429, "limited", {"Retry-After": "7"}, None)
    assert factors._retry_delay(error, 1) == 7.0


def test_429_retry_delay_is_bounded() -> None:
    error = HTTPError("https://example.test", 429, "limited", {}, None)
    assert factors._retry_delay(error, 1) == 15.0
    assert factors._retry_delay(error, 8) == 120.0


def test_fetch_rejects_unknown_symbol(tmp_path) -> None:
    try:
        factors.fetch_v14_funding("UNKNOWN", date(2025, 1, 1), date(2025, 1, 2), tmp_path / "x.csv")
    except factors.ExternalFactorDataError as exc:
        assert "mapping" in str(exc)
    else:
        raise AssertionError("unknown symbol was accepted")
