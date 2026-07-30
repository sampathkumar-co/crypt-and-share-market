from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tradebot.backtest import crypto_multiregime_4h as base
from tradebot.backtest import crypto_multiregime_4h_calendar_funding as calendar


def funding_series(
    start: datetime,
    days: int,
    spacing_hours: int,
    value: float,
) -> dict[datetime, float]:
    count = days * 24 // spacing_hours + 1
    return {
        start + timedelta(hours=spacing_hours * index): value
        for index in range(count)
    }


def test_snapshots_require_seven_days_plus_full_120_day_history() -> None:
    start = datetime(2023, 1, 1)
    snapshots = calendar.build_calendar_funding_snapshots(
        funding_series(start, 140, 8, -0.002)
    )
    assert snapshots
    assert min(snapshots) == start + timedelta(days=126, hours=20)


def test_missing_settlement_buckets_are_zero_cashflow() -> None:
    start = datetime(2023, 1, 1)
    snapshots = calendar.build_calendar_funding_snapshots(
        funding_series(start, 140, 8, -0.002)
    )
    current, tenth, rolling_median = snapshots[max(snapshots)]
    assert current == pytest.approx(-0.001)
    assert tenth == pytest.approx(-0.001)
    assert rolling_median == pytest.approx(-0.001)


def test_future_funding_cannot_change_an_earlier_snapshot() -> None:
    start = datetime(2023, 1, 1)
    original = funding_series(start, 140, 8, -0.002)
    left = calendar.build_calendar_funding_snapshots(original)
    anchor = sorted(left)[10]
    extended = dict(original)
    future_start = max(original) + timedelta(hours=8)
    extended.update(
        {
            future_start + timedelta(hours=8 * index): 10.0
            for index in range(100)
        }
    )
    right = calendar.build_calendar_funding_snapshots(extended)
    assert right[anchor] == left[anchor]


def test_runtime_snapshot_uses_completed_four_hour_bucket() -> None:
    start = datetime(2023, 1, 1)
    store = base.ExternalStore(
        stablecoin={},
        funding={"APTUSDT": funding_series(start, 140, 8, -0.002)},
        macro={},
        manifest={},
        manifest_fingerprint="calendar-test",
    )
    anchor = start + timedelta(days=130)
    result = calendar.calendar_funding_snapshot(
        store,
        "APTUSDT",
        anchor + timedelta(hours=3, minutes=59),
    )
    expected = calendar.build_calendar_funding_snapshots(
        store.funding["APTUSDT"]
    )[base._four_hour_bucket(anchor)]
    assert result == expected


def test_report_is_labelled_v142() -> None:
    class StubReport:
        schema_version = "old"

    report = StubReport()
    labelled = calendar._label_report(report)  # type: ignore[arg-type]
    assert labelled.schema_version == "1.4.2"
