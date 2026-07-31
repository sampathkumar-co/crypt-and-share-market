from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from tradebot.research import historical_proxy_screen_v25 as screen


def _zip_csv(rows: list[list[object]], name: str = "fixture.csv") -> bytes:
    text = io.StringIO()
    writer = csv.writer(text, lineterminator="\n")
    writer.writerows(rows)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(name, text.getvalue())
    return buffer.getvalue()


def _archive(rows: list[list[object]]) -> screen.DownloadedArchive:
    content = _zip_csv(rows)
    return screen.DownloadedArchive("https://example.invalid/fixture.zip", "a" * 64, content)


def test_spot_microsecond_kline_timestamp_is_normalized() -> None:
    rows = [[
        1780272000000000,
        "100",
        "102",
        "99",
        "101",
        "10",
        1780275599999999,
        "1000",
        "20",
        "6",
        "600",
        "0",
    ]]

    bars = screen._parse_klines(_archive(rows))

    hour = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert bars[hour].open == 100.0
    assert bars[hour].close == 101.0
    assert bars[hour].taker_buy_quote_volume == 600.0


def test_funding_and_metrics_headers_are_parsed() -> None:
    funding = _archive([
        ["calc_time", "funding_interval_hours", "last_funding_rate"],
        [1780272000000, "8", "-0.0001"],
    ])
    metrics = _archive([
        ["create_time", "symbol", "sum_open_interest"],
        ["2026-06-01 00:05:00", "BTCUSDT", "1234.5"],
        ["2026-06-01 00:55:00", "BTCUSDT", "1250.0"],
    ])

    funding_values = screen._parse_funding(funding)
    metric_values = screen._parse_metrics(metrics)

    assert next(iter(funding_values.values())) == -0.0001
    assert metric_values[datetime(2026, 6, 1, tzinfo=timezone.utc)] == 1250.0


def test_imbalance_is_bounded_and_zero_for_empty_volume() -> None:
    assert screen._imbalance(0.0, 0.0) == 0.0
    assert screen._imbalance(100.0, 75.0) == 0.5
    assert screen._imbalance(100.0, 200.0) == 1.0


def _bar(hour: datetime, opening: float) -> screen.HourlyBar:
    return screen.HourlyBar(
        hour=hour,
        open=opening,
        high=opening,
        low=opening,
        close=opening,
        quote_volume=100.0,
        taker_buy_quote_volume=50.0,
    )


def test_event_evaluation_uses_next_open_and_costs() -> None:
    decision = datetime(2026, 6, 10, 12, tzinfo=timezone.utc)
    spot = {asset: {} for asset in screen.ASSETS}
    for asset in screen.ASSETS:
        spot[asset][decision + timedelta(hours=1)] = _bar(decision + timedelta(hours=1), 100.0)
        spot[asset][decision + timedelta(hours=5)] = _bar(decision + timedelta(hours=5), 110.0)
    event = screen.ScreenEvent(decision, "ETH", "residual_momentum_microstructure", 0.15, "x")

    result = screen.evaluate_events([event], spot, horizon=4, cost=0.002)

    assert result["accepted_event_count"] == 1
    assert result["cohort_count"] == 1
    assert result["gross_compounded_return"] == pytest.approx(0.015)
    assert result["net_compounded_return"] == pytest.approx(0.0147)
    assert result["event_win_rate"] == 1.0


def test_missing_exit_price_excludes_cohort() -> None:
    decision = datetime(2026, 6, 10, 12, tzinfo=timezone.utc)
    spot = {asset: {} for asset in screen.ASSETS}
    for asset in screen.ASSETS:
        spot[asset][decision + timedelta(hours=1)] = _bar(decision + timedelta(hours=1), 100.0)
    event = screen.ScreenEvent(decision, "SOL", "funding_basis_state_transition", 0.15, "x")

    result = screen.evaluate_events([event], spot, horizon=4, cost=0.002)

    assert result["cohort_count"] == 0
    assert result["excluded_cohorts"][0]["reason"] == "missing_exit_price:SOL"


def test_report_contract_never_authorizes_execution(monkeypatch) -> None:
    monkeypatch.setattr(screen, "download_historical_inputs", lambda max_workers=8: ({}, []))
    monkeypatch.setattr(screen, "assemble_inputs", lambda downloaded: ([], {asset: {} for asset in screen.ASSETS}, []))
    monkeypatch.setattr(screen, "build_events", lambda frames: ([], []))

    report = screen.run_screen()

    assert report["mode"] == "HISTORICAL_PROXY_SCREEN_ONLY"
    assert report["paper_only"] is True
    assert report["authorizes_trading"] is False
    assert report["authorizes_shadow_paper"] is False
    assert report["changes_track_a"] is False
    assert report["cannot_replace_forward_evidence"] is True
    canonical = dict(report)
    expected = canonical.pop("report_sha256")
    actual = screen.hashlib.sha256(screen.canonical_json(canonical).encode("utf-8")).hexdigest()
    assert actual == expected


def test_protocol_fixes_dates_and_proxy_disclosure() -> None:
    protocol = screen.PROTOCOL_PATH.read_text(encoding="utf-8")
    assert "2026-06-01 00:00 UTC" in protocol
    assert "2026-06-30 23:00 UTC" in protocol
    assert "HISTORICAL_PROXY_SCREEN_ONLY" in protocol
    assert "must never write to" in protocol
    assert "forward-data/v2" in protocol
    assert "liquidity proxies" in protocol
