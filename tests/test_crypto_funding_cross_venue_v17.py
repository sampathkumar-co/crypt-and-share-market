from __future__ import annotations

import json
from datetime import datetime

import pytest

from tradebot.backtest import crypto_funding_cross_venue_v17 as v17
from tradebot.backtest import crypto_multiregime_4h as base


def period(number: int, net_return: float, turnover: float) -> base.MultiRegimePeriod:
    return base.MultiRegimePeriod(
        variant="primary_balanced",
        mode="discovery",
        period=number,
        test_start=f"2024-08-{number:02d}T00:00:00",
        test_end=f"2024-08-{number:02d}T04:00:00",
        net_return=net_return,
        stressed_return=net_return - turnover * 0.0015,
        equal_weight_buy_hold_return=0.0,
        excess_vs_equal_weight=net_return,
        max_drawdown=0.01,
        turnover=turnover,
        transactions=2,
        active=True,
        average_cash_weight=0.50,
        selected_symbols=["APTUSDT"],
        sleeve_entries={"trend": 0, "range": 0, "funding": 1},
        traded_notional_by_asset={"APTUSDT": 1000.0},
        total_fees=1.0,
        total_slippage=1.0,
        total_tax=0.0,
    )


def test_schema_and_split_are_frozen() -> None:
    assert v17.SCHEMA_VERSION == "1.7.1"
    assert v17.PRIMARY_CONFIG.total_bars == 3504
    assert v17.PRIMARY_CONFIG.warmup_bars == 600
    assert v17.PRIMARY_CONFIG.discovery_periods == 5
    assert v17.PRIMARY_CONFIG.discovery_test_bars == 390
    assert v17.PRIMARY_CONFIG.embargo_bars == 234
    assert v17.PRIMARY_CONFIG.holdout_bars == 720
    assert (
        v17.PRIMARY_CONFIG.warmup_bars
        + v17.PRIMARY_CONFIG.discovery_periods * v17.PRIMARY_CONFIG.discovery_test_bars
        + v17.PRIMARY_CONFIG.embargo_bars
        + v17.PRIMARY_CONFIG.holdout_bars
        == v17.PRIMARY_CONFIG.total_bars
    )


def test_expected_grid_matches_frozen_dates() -> None:
    stamps = v17._expected_timestamps()
    assert len(stamps) == 3504
    assert stamps[0] == datetime(2024, 4, 18)
    assert stamps[-1] == datetime(2025, 11, 22, 20)


def test_candidate_delegates_to_exact_funding_sleeve() -> None:
    assert v17._funding_variant("primary_balanced").enabled_sleeves == ("funding",)


def test_sizing_profiles_respect_exposure_contracts() -> None:
    for _, config in v17.PROFILE_CONFIGS:
        assert config.max_positions * config.max_asset_weight <= 1.0 - config.min_cash_reserve + 1e-12
    assert v17.DEFENSIVE_CONFIG.max_asset_weight == pytest.approx(0.125)
    assert v17.DEFENSIVE_CONFIG.min_cash_reserve == pytest.approx(0.75)


def test_leave_one_period_out_uses_other_four_periods() -> None:
    periods = [period(index + 1, value, 1.0) for index, value in enumerate((0.01, 0.02, -0.01, 0.03, 0.04))]
    result = v17._leave_one_period_out(periods)
    assert result["period_1"] == pytest.approx((0.02 - 0.01 + 0.03 + 0.04) / 4)
    assert result["period_5"] == pytest.approx((0.01 + 0.02 - 0.01 + 0.03) / 4)
    assert len(result) == 5


def test_profile_summary_records_blocks_and_double_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    generated = [
        period(1, 0.01, 1.0),
        period(2, 0.03, 1.0),
        period(3, 0.02, 1.0),
        period(4, 0.04, 1.0),
        period(5, 0.06, 1.0),
    ]
    monkeypatch.setattr(
        base,
        "_simulate_period",
        lambda histories, store, variant, number, config, mode: generated[number - 1],
    )
    result = v17._profile_summary(
        {"APTUSDT": []},
        base.ExternalStore({}, {}, {}, {}, "external"),
        "primary_balanced",
        v17.PRIMARY_CONFIG,
        "discovery",
    )
    assert result.first_block_average == pytest.approx(0.02)
    assert result.second_block_average == pytest.approx(0.04)
    assert result.average_double_cost_stressed_return == pytest.approx(0.032 - 0.003)


def test_holdout_is_locked_without_accepted_replication(tmp_path) -> None:
    replication = tmp_path / "replication.json"
    replication.write_text(
        json.dumps({"accepted": False, "eligible_for_holdout": False}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="holdout is locked"):
        v17.evaluate_holdout(
            tmp_path / "prices",
            tmp_path / "external",
            replication,
            "fingerprint",
        )


def test_report_is_paper_only_by_default() -> None:
    fields = v17.CrossVenueReport.__dataclass_fields__
    assert fields["paper_only"].default is True
    assert fields["authorizes_real_trading"].default is False
