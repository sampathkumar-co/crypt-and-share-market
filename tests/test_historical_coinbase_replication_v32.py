from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tradebot.research import historical_coinbase_replication_v32 as v32
from tradebot.research import historical_yield_trend_scheduled_execution_v312 as execution


def _epoch(day: datetime) -> int:
    return int(day.timestamp())


def _row(day: datetime, price: float) -> list[float]:
    return [_epoch(day), price - 1.0, price + 1.0, price, price + 0.5, 10.0]


def test_request_ranges_cover_every_day_without_overlap() -> None:
    ranges = v32._request_ranges()
    assert ranges[0][0] == v32.DATA_START
    assert ranges[-1][1] == v32.EXIT_DATE
    days: list[datetime] = []
    for start, end in ranges:
        assert (end - start).days + 1 <= 250
        cursor = start
        while cursor <= end:
            days.append(cursor)
            cursor += timedelta(days=1)
    assert days == v32._days(v32.DATA_START, v32.EXIT_DATE)
    assert len(days) == len(set(days))
    assert len(ranges) == 9


def test_coinbase_parser_accepts_reverse_order_and_filters_extra_rows() -> None:
    start = datetime(2021, 1, 1, tzinfo=timezone.utc)
    end = datetime(2021, 1, 3, tzinfo=timezone.utc)
    payload = [
        _row(end + timedelta(days=1), 104.0),
        _row(end, 103.0),
        _row(start + timedelta(days=1), 102.0),
        _row(start, 101.0),
        _row(start - timedelta(days=1), 100.0),
    ]
    bars = v32._parse_coinbase_candles(
        json.dumps(payload).encode("utf-8"),
        asset="BTC",
        requested_start=start,
        requested_end=end,
    )
    assert sorted(bars) == [start, start + timedelta(days=1), end]
    assert bars[start].open == 101.0
    assert bars[end].close == 103.5


def test_coinbase_parser_rejects_conflicting_duplicate() -> None:
    day = datetime(2021, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(v32.CoinbaseReplicationV32Error):
        v32._parse_coinbase_candles(
            json.dumps([_row(day, 100.0), _row(day, 110.0)]).encode(),
            asset="ETH",
            requested_start=day,
            requested_end=day,
        )


def test_frozen_model_and_integrity_dependency_are_exact() -> None:
    assert v32.FROZEN_MODEL.model_id == v32.EXPECTED_MODEL_ID
    assert v32.FROZEN_MODEL.sma_length == 100
    assert v32.FROZEN_MODEL.rebalance_days == 10
    assert v32.FROZEN_MODEL.top_n == 1
    assert v32.FROZEN_MODEL.maximum_exposure == 0.10
    assert v32.FROZEN_MODEL.volatility_target == 0.02
    assert v32.FROZEN_MODEL.drawdown_brake == 0.20
    assert v32.EXPECTED_V312_REPORT_SHA256 == (
        "90dea7bcc12274146f730ba5a5cd9f93179ff944211ff07de849aca68e468c22"
    )
    assert v32._summarize is execution.summarize_years
    assert v32._evaluate_gates is execution.evaluate_integrity_gates


def _summary(*, losing_active_year: bool = False) -> dict[str, object]:
    years = ["2021", "2022", "2023", "2024", "2025"]
    active = ["2021", "2023", "2024", "2025"]
    cash = {year: 0.02 for year in years}
    returns = {
        "2021": 0.04,
        "2022": 0.02,
        "2023": 0.04,
        "2024": 0.05,
        "2025": 0.04,
    }
    if losing_active_year:
        returns["2023"] = 0.01
    excess = {year: returns[year] - cash[year] for year in years}
    actions = {year: (5 if year in active else 0) for year in years}
    return {
        "window_returns": returns,
        "cash_window_returns": cash,
        "excess_window_returns": excess,
        "window_action_days": actions,
        "active_years": active,
        "inactive_years": ["2022"],
        "active_year_count": 4,
        "selected_assets": ["BTC", "ETH"],
        "crypto_action_days": 20,
        "net_compounded_return": 0.20,
        "cash_benchmark_compounded_return": 0.10,
        "excess_compounded_return": 0.10,
        "maximum_drawdown": 0.02,
        "maximum_positive_asset_share": 0.60,
        "maximum_positive_year_share": 0.40,
    }


def test_gate_allows_one_exact_cash_year() -> None:
    summary = _summary()
    assert all(v32._evaluate_gates(summary, dict(summary)).values())


def test_gate_rejects_active_year_below_cash() -> None:
    summary = _summary(losing_active_year=True)
    gates = v32._evaluate_gates(summary, dict(summary))
    assert gates["five_positive_standard_years"] is True
    assert gates["every_active_standard_year_beats_cash"] is False


def test_candle_url_uses_documented_daily_granularity() -> None:
    start = datetime(2021, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=249)
    url = v32._candle_url("BTC-USD", start, end)
    assert "/products/BTC-USD/candles?" in url
    assert "granularity=86400" in url
    assert "start=2021-01-01T00%3A00%3A00Z" in url
    assert "end=2021-09-08T00%3A00%3A00Z" in url
