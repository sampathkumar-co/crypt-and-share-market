from __future__ import annotations

import csv
from datetime import date, datetime, time, timezone
from pathlib import Path

import pytest

from tradebot.data import crypto_external_factors_hyperliquid as hyperliquid
from tradebot.data.crypto_external_factors import ExternalFactorDataError


def _stamp(day: date, hour: int = 0) -> int:
    return int(
        datetime.combine(day, time(hour=hour), tzinfo=timezone.utc).timestamp()
        * 1000
    )


def test_hyperliquid_funding_writes_frozen_csv_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = date(2025, 5, 27)
    end = date(2025, 5, 28)
    calls: list[dict[str, object]] = []

    def fake_post(body: dict[str, object], **_: object) -> list[dict[str, object]]:
        calls.append(body)
        return [
            {
                "time": _stamp(start),
                "fundingRate": "0.00001",
                "premium": "0",
            },
            {
                "time": _stamp(end, 23),
                "fundingRate": "-0.00002",
                "premium": "0",
            },
        ]

    monkeypatch.setattr(hyperliquid, "_post_json", fake_post)
    destination = tmp_path / "bybit" / "LTCUSDT.csv"
    source = hyperliquid.fetch_hyperliquid_funding(
        "LTCUSDT", start, end, destination
    )

    assert source.provider == "hyperliquid-public-info"
    assert source.source_id == "LTCUSDT"
    assert source.first_date == start.isoformat()
    assert source.last_date == end.isoformat()
    assert source.rows == 2
    assert source.sha256
    assert calls == [
        {
            "type": "fundingHistory",
            "coin": "LTC",
            "startTime": _stamp(start),
            "endTime": _stamp(end + hyperliquid.timedelta(days=1)) - 1,
        }
    ]

    with destination.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == ["timestamp", "funding_rate"]
    assert rows[0]["funding_rate"] == "1e-05"
    assert rows[1]["funding_rate"] == "-2e-05"


def test_hyperliquid_rejects_incomplete_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = date(2025, 5, 27)
    end = date(2025, 5, 28)

    monkeypatch.setattr(
        hyperliquid,
        "_post_json",
        lambda body, **kwargs: [
            {"time": _stamp(start), "fundingRate": "0.00001"}
        ],
    )

    with pytest.raises(ExternalFactorDataError, match="Incomplete Hyperliquid"):
        hyperliquid.fetch_hyperliquid_funding(
            "LTCUSDT", start, end, tmp_path / "LTCUSDT.csv"
        )


def test_hyperliquid_rejects_unknown_symbol(tmp_path: Path) -> None:
    with pytest.raises(ExternalFactorDataError, match="mapping"):
        hyperliquid.fetch_hyperliquid_funding(
            "UNKNOWNUSDT",
            date(2025, 5, 27),
            date(2025, 5, 28),
            tmp_path / "unknown.csv",
        )


def test_hyperliquid_mapping_covers_frozen_universe() -> None:
    assert set(hyperliquid.HYPERLIQUID_COINS) == {
        "LTCUSDT",
        "BCHUSDT",
        "LINKUSDT",
        "XLMUSDT",
        "ETCUSDT",
        "ATOMUSDT",
        "UNIUSDT",
        "AAVEUSDT",
    }
