from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradebot.research import dual_source_consensus_v63 as v63
from tradebot.research import historical_proxy_screen_v25 as v25


def _bar(day: datetime, value: float) -> v25.HourlyBar:
    return v25.HourlyBar(
        hour=day,
        open=value,
        high=value,
        low=value,
        close=value,
        quote_volume=1.0,
        taker_buy_quote_volume=0.5,
    )


def test_dual_target_uses_lower_source_weight_per_asset():
    target = v63._dual_target(
        {"BTC": 0.08, "ETH": 0.02},
        {"BTC": 0.05, "ETH": 0.04},
    )
    assert target == {"BTC": 0.05, "ETH": 0.02}


def test_dual_target_cannot_add_source_unsupported_asset():
    assert v63._dual_target({"BTC": 0.10}, {"ETH": 0.10}) == {}


def test_mean_target_requires_all_frozen_members():
    import pytest

    with pytest.raises(v63.DualSourceConsensusV63Error):
        v63._mean_target({})


def test_execution_carries_natural_drift_without_source_decision():
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    days = [base + timedelta(days=index) for index in range(4)]
    bars = {
        "BTC": {
            day: _bar(day, value)
            for day, value in zip(days, [100.0, 110.0, 120.0, 130.0], strict=True)
        },
        "ETH": {day: _bar(day, 100.0) for day in days},
    }
    left = {
        days[0]: v63.SourceTargetDay(days[0], {"BTC": 0.10}, True),
        days[1]: v63.SourceTargetDay(days[1], {"BTC": 0.11}, False),
        days[2]: v63.SourceTargetDay(days[2], {"BTC": 0.12}, False),
    }
    right = {
        days[0]: v63.SourceTargetDay(days[0], {"BTC": 0.10}, True),
        days[1]: v63.SourceTargetDay(days[1], {"BTC": 0.11}, False),
        days[2]: v63.SourceTargetDay(days[2], {"BTC": 0.12}, False),
    }
    cash = {day: 0.0 for day in days}
    result = v63.simulate_execution(
        bars,
        left,
        right,
        cash,
        days[0],
        days[2],
        0.0,
    )
    # Initial entry plus terminal liquidation only.
    assert result.action_days == 2
    assert result.net_return > 0.0


def test_misaligned_source_paths_fail_closed():
    import pytest

    day = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(v63.DualSourceConsensusV63Error):
        v63.simulate_execution(
            {},
            {day: v63.SourceTargetDay(day, {}, False)},
            {},
            {},
            day,
            day,
            0.0,
        )
