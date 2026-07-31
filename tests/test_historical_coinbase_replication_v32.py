from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from tradebot.research import historical_coinbase_replication_v32 as v32


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
    payload = [_row(day, 100.0), _row(day, 110.0)]
    with pytest.raises(v32.CoinbaseReplicationV32Error):
        v32._parse_coinbase_candles(
            json.dumps(payload).encode("utf-8"),
            asset="ETH",
            requested_start=day,
            requested_end=day,
        )


def test_frozen_model_exactly_matches_v31_selection() -> None:
    assert v32.FROZEN_MODEL.model_id == v32.EXPECTED_MODEL_ID
    assert v32.FROZEN_MODEL.sma_length == 100
    assert v32.FROZEN_MODEL.rebalance_days == 10
    assert v32.FROZEN_MODEL.top_n == 1
    assert v32.FROZEN_MODEL.maximum_exposure == 0.10
    assert v32.FROZEN_MODEL.volatility_target == 0.02
    assert v32.FROZEN_MODEL.drawdown_brake == 0.20


def _summary(
    *,
    active_years: list[str],
    excess: dict[str, float],
    returns: dict[str, float],
    cash: dict[str, float],
) -> dict[str, object]:
    actions = {
        year: (10 if year in active_years else 0)
        for year in returns
    }
    return {
        "window_returns": returns,
        "cash_window_returns": cash,
        "excess_window_returns": excess,
        "window_action_days": actions,
        "active_years": active_years,
        "inactive_years": [year for year in returns if year not in active_years],
        "active_year_count": len(active_years),
        "selected_assets": ["BTC", "ETH"],
        "asset_net_contribution": {"BTC": 0.06, "ETH": 0.04},
        "crypto_contribution": 0.10,
        "cash_contribution": 0.10,
        "crypto_turnover": 2.0,
        "crypto_action_days": sum(actions.values()),
        "net_compounded_return": 0.20,
        "cash_benchmark_compounded_return": 0.10,
        "excess_compounded_return": 0.10,
        "maximum_drawdown": 0.02,
        "maximum_positive_asset_share": 0.60,
        "maximum_positive_year_share": 0.40,
    }


def test_gate_allows_exact_cash_in_one_inactive_year() -> None:
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
    excess = {year: returns[year] - cash[year] for year in years}
    standard = _summary(
        active_years=active,
        excess=excess,
        returns=returns,
        cash=cash,
    )
    stress = _summary(
        active_years=active,
        excess=excess,
        returns=returns,
        cash=cash,
    )
    gates = v32._evaluate_gates(standard, stress)
    assert all(gates.values())


def test_gate_rejects_active_year_that_fails_to_beat_cash() -> None:
    years = ["2021", "2022", "2023", "2024", "2025"]
    active = ["2021", "2023", "2024", "2025"]
    cash = {year: 0.02 for year in years}
    returns = {year: 0.04 for year in years}
    returns["2022"] = 0.02
    returns["2023"] = 0.01
    excess = {year: returns[year] - cash[year] for year in years}
    standard = _summary(
        active_years=active,
        excess=excess,
        returns=returns,
        cash=cash,
    )
    stress = _summary(
        active_years=active,
        excess=excess,
        returns=returns,
        cash=cash,
    )
    gates = v32._evaluate_gates(standard, stress)
    assert gates["five_positive_standard_years"] is True
    assert gates["every_active_standard_year_beats_cash"] is False


def test_candle_url_uses_one_day_granularity_and_bounded_range() -> None:
    start = datetime(2021, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=249)
    url = v32._candle_url("BTC-USD", start, end)
    assert "/products/BTC-USD/candles?" in url
    assert "granularity=86400" in url
    assert "start=2021-01-01T00%3A00%3A00Z" in url
    assert "end=2021-09-08T00%3A00%3A00Z" in url


def test_inventory_fingerprint_is_order_stable() -> None:
    inventory = [
        {"key": "b", "sha256": "2"},
        {"key": "a", "sha256": "1"},
    ]
    inventory.sort(key=lambda item: item["key"])
    first = hashlib.sha256(
        v32.canonical_json(inventory).encode("utf-8")
    ).hexdigest()
    second = hashlib.sha256(
        v32.canonical_json(list(reversed(inventory))).encode("utf-8")
    ).hexdigest()
    assert first != second
