from __future__ import annotations

from urllib.error import URLError

import pytest

from tradebot.research import historical_yield_trend_v31 as v31
from tradebot.research import historical_yield_trend_v31_runner as runner
from tradebot.research import historical_yield_trend_v311_transport as transport


class FakeResponse:
    status = 200

    def __init__(self, content: bytes):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.content


def _h15_content() -> bytes:
    return (
        b'"Series Description","three month"\n'
        b'"Unit:","Percent:_Per_Year"\n'
        b'"Unique Identifier:","H15/H15/RIFLGFCM03_N.B"\n'
        b'"Time Period","RIFLGFCM03_N.B"\n'
        b"2017-08-31,1.00\n"
        b"2025-12-31,3.25\n"
    )


def test_h15_parser_extracts_exact_series_and_dates() -> None:
    rates = transport.parse_federal_reserve_h15_rates(_h15_content())
    dates = sorted(rates)
    assert dates[0].date().isoformat() == "2017-08-31"
    assert dates[-1].date().isoformat() == "2025-12-31"
    assert rates[dates[0]] == 0.01
    assert rates[dates[-1]] == 0.0325


def test_transport_uses_h15_only_after_both_fred_urls_fail(
    monkeypatch,
) -> None:
    calls: list[tuple[str, float]] = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        if request.full_url != transport.FED_H15_URL:
            raise URLError("FRED unavailable")
        return FakeResponse(_h15_content())

    monkeypatch.setattr(transport, "urlopen", fake_urlopen)
    monkeypatch.setattr(transport, "MIN_OBSERVATIONS", 2)
    monkeypatch.setattr(transport.time, "sleep", lambda seconds: None)

    normalized, inventory = transport.download_cash_series_with_resilience()

    assert [item[0] for item in calls] == [
        v31.FRED_URL,
        transport.FRED_FALLBACK_URL,
        transport.FED_H15_URL,
    ]
    assert [item[1] for item in calls] == [15.0, 15.0, 30.0]
    assert normalized.startswith(b"observation_date,DGS3MO\n")
    assert inventory["key"] == "cash:DGS3MO"
    assert inventory["url"] == transport.FED_H15_URL
    audit = transport.TRANSPORT_AUDIT
    assert audit["attempt_count"] == 3
    assert audit["selected_source"] == "federal_reserve_h15"
    assert audit["series_id"] == transport.FED_H15_SERIES
    assert audit["raw_sha256"]
    assert audit["normalized_sha256"]
    assert audit["observation_count"] == 2
    assert audit["first_date"] == "2017-08-31"
    assert audit["last_date"] == "2025-12-31"


def test_h15_parser_fails_closed_when_exact_series_is_missing() -> None:
    content = (
        b'"Time Period","RIFLGFCM06_N.B"\n'
        b"2017-08-31,1.00\n"
    )
    with pytest.raises(transport.CashTransportError):
        transport.parse_federal_reserve_h15_rates(content)


def test_h15_parser_rejects_duplicate_dates() -> None:
    content = (
        b'"Time Period","RIFLGFCM03_N.B"\n'
        b"2017-08-31,1.00\n"
        b"2017-08-31,1.01\n"
    )
    with pytest.raises(transport.CashTransportError):
        transport.parse_federal_reserve_h15_rates(content)


def test_guard_restores_parser_and_downloader(monkeypatch) -> None:
    captured: dict[str, bool] = {}

    def fake_run(*, max_workers: int):
        captured["parser"] = v31.parse_cash_rates is transport.parse_fred_rates
        captured["downloader"] = (
            v31._download_fred
            is transport.download_cash_series_with_resilience
        )
        return {
            "fingerprints": {
                "protocol_sha256": "a",
                "addendum_sha256": "b",
                "implementation_sha256": "c",
                "chosen_model_sha256": "d",
            },
            "report_sha256": "stale",
        }

    original_parser = v31.parse_cash_rates
    original_downloader = v31._download_fred
    monkeypatch.setattr(v31, "run_overlay", fake_run)
    report = runner.run_guarded_overlay(max_workers=2)

    assert captured == {"parser": True, "downloader": True}
    assert v31.parse_cash_rates is original_parser
    assert v31._download_fred is original_downloader
    assert report["cash_transport_policy"] == transport.CASH_TRANSPORT_POLICY
    assert report["cash_source_policy"] == transport.CASH_SOURCE_POLICY
    assert report["transport_addendum_path"].endswith(
        "V311_FEDERAL_RESERVE_H15_TRANSPORT_ADDENDUM.md"
    )
    assert report["fingerprints"]["cash_transport_sha256"]
    assert report["fingerprints"]["transport_addendum_sha256"]
    assert report["report_sha256"] != "stale"
