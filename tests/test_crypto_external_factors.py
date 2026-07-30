from __future__ import annotations

import json
from datetime import date

import pytest

from tradebot.data import crypto_external_factors as external


def test_coinmetrics_filter_preserves_requested_metrics(monkeypatch, tmp_path) -> None:
    payload = (
        "time,AdrActCnt,TxCnt,Other\n"
        "2025-01-01,10,20,1\n"
        "2025-01-02,,25,2\n"
        "2025-02-01,30,40,3\n"
    ).encode()
    monkeypatch.setattr(external, "_request_bytes", lambda url: payload)
    path = tmp_path / "ltc.csv"
    source = external.fetch_coinmetrics_filtered(
        "ltc",
        ("AdrActCnt", "TxCnt"),
        date(2025, 1, 1),
        date(2025, 1, 31),
        path,
    )
    assert source.rows == 2
    assert source.first_date == "2025-01-01"
    assert source.last_date == "2025-01-02"
    assert source.missing_values == 1
    assert path.read_text().splitlines()[0] == "date,AdrActCnt,TxCnt"


def test_bybit_funding_paginates_and_deduplicates(monkeypatch, tmp_path) -> None:
    pages = [
        [
            {"fundingRateTimestamp": "1735776000000", "fundingRate": "0.0001"},
            {"fundingRateTimestamp": "1735689600000", "fundingRate": "0.0002"},
        ],
        [
            {"fundingRateTimestamp": "1735689600000", "fundingRate": "0.0002"},
            {"fundingRateTimestamp": "1735603200000", "fundingRate": "-0.0001"},
        ],
    ]

    def page(symbol: str, end_ms: int):
        return pages.pop(0) if pages else []

    monkeypatch.setattr(external, "_fetch_bybit_page", page)
    path = tmp_path / "LTCUSDT.csv"
    source = external.fetch_bybit_funding(
        "LTCUSDT",
        date(2024, 12, 31),
        date(2025, 1, 2),
        path,
    )
    assert source.rows == 3
    assert len(path.read_text().splitlines()) == 4


def test_fred_filter_drops_missing_values(monkeypatch, tmp_path) -> None:
    payload = b"observation_date,VIXCLS\n2025-01-01,.\n2025-01-02,18.5\n2025-02-01,20\n"
    monkeypatch.setattr(external, "_request_bytes", lambda url: payload)
    path = tmp_path / "VIXCLS.csv"
    source = external.fetch_fred_series(
        "VIXCLS",
        date(2025, 1, 1),
        date(2025, 1, 31),
        path,
    )
    assert source.rows == 1
    assert source.first_date == "2025-01-02"


def test_manifest_verification_fails_after_tamper(tmp_path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("date,value\n2025-01-01,1\n", encoding="utf-8")
    manifest = {
        "schema_version": "1.0",
        "paper_only": True,
        "files": [
            {
                "relative_path": "source.csv",
                "sha256": external.sha256_file(source),
            }
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    external.verify_external_manifest(tmp_path)
    source.write_text("date,value\n2025-01-01,2\n", encoding="utf-8")
    with pytest.raises(external.ExternalFactorDataError):
        external.verify_external_manifest(tmp_path)
