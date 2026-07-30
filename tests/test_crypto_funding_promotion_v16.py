from __future__ import annotations

from datetime import datetime

import pytest

from tradebot.backtest import crypto_funding_promotion_v16 as v16
from tradebot.backtest import crypto_multiregime_4h as base


def period(number: int, net_return: float, turnover: float) -> base.MultiRegimePeriod:
    return base.MultiRegimePeriod(
        variant="primary_balanced",
        mode="discovery",
        period=number,
        test_start=f"2024-01-{number:02d}T00:00:00",
        test_end=f"2024-01-{number:02d}T04:00:00",
        net_return=net_return,
        stressed_return=net_return - turnover * v16.STANDARD_COST_PER_TURNOVER,
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


def test_schema_and_cost_stresses_are_frozen() -> None:
    assert v16.SCHEMA_VERSION == "1.6.1"
    assert v16.STANDARD_COST_PER_TURNOVER == pytest.approx(0.0015)
    assert v16.DOUBLE_COST_PER_TURNOVER == pytest.approx(0.0030)


def test_candidate_delegates_to_unchanged_funding_sleeve() -> None:
    variant = v16._funding_variant("primary_balanced")
    assert variant.enabled_sleeves == ("funding",)


def test_fixed_profiles_respect_cash_and_weight_contracts() -> None:
    assert v16.PRIMARY_CONFIG.max_positions == 2
    assert v16.PRIMARY_CONFIG.max_asset_weight == pytest.approx(0.25)
    assert v16.PRIMARY_CONFIG.min_cash_reserve == pytest.approx(0.50)
    assert v16.ORIGINAL_CONFIG.max_positions == 3
    assert v16.ORIGINAL_CONFIG.max_asset_weight == pytest.approx(0.25)
    assert v16.ORIGINAL_CONFIG.min_cash_reserve == pytest.approx(0.25)
    assert v16.DEFENSIVE_CONFIG.max_positions == 2
    assert v16.DEFENSIVE_CONFIG.max_asset_weight == pytest.approx(0.125)
    assert v16.DEFENSIVE_CONFIG.min_cash_reserve == pytest.approx(0.75)
    for _, config in v16.PROFILE_CONFIGS:
        assert config.max_positions * config.max_asset_weight <= 1.0 - config.min_cash_reserve + 1e-12


def test_leave_one_period_out_uses_other_five_periods() -> None:
    periods = [period(index + 1, value, 1.0) for index, value in enumerate((0.01, 0.02, -0.01, 0.03, 0.04, 0.05))]
    result = v16._leave_one_period_out(periods)
    assert result["period_1"] == pytest.approx((0.02 - 0.01 + 0.03 + 0.04 + 0.05) / 5)
    assert result["period_6"] == pytest.approx((0.01 + 0.02 - 0.01 + 0.03 + 0.04) / 5)
    assert len(result) == 6


def test_profile_summary_records_double_cost_stress(monkeypatch: pytest.MonkeyPatch) -> None:
    generated = [period(index + 1, 0.02, 2.0) for index in range(6)]
    monkeypatch.setattr(
        base,
        "_simulate_period",
        lambda histories, store, variant, number, config, mode: generated[number - 1],
    )
    store = base.ExternalStore({}, {}, {}, {}, "external")
    result = v16._profile_summary(
        {"APTUSDT": []},
        store,
        "primary_balanced",
        v16.PRIMARY_CONFIG,
        "discovery",
    )
    assert result.average_double_cost_stressed_return == pytest.approx(0.02 - 2.0 * 0.0030)
    assert result.summary.average_stressed_return == pytest.approx(0.02 - 2.0 * 0.0015)


def test_positive_profile_requires_both_cost_stresses() -> None:
    periods = [period(index + 1, 0.01, 1.0) for index in range(6)]
    summary = base._summarize("primary_balanced", periods)
    result = v16.PromotionProfileResult(
        profile="primary_balanced",
        config={},
        summary=summary,
        average_double_cost_stressed_return=0.001,
    )
    assert result.summary.average_return > 0
    assert result.summary.average_stressed_return > 0
    assert result.average_double_cost_stressed_return > 0


def test_holdout_is_locked_without_accepted_promotion(tmp_path) -> None:
    promotion = tmp_path / "promotion.json"
    promotion.write_text(
        '{"accepted": false, "eligible_for_holdout": false}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="holdout is locked"):
        v16.evaluate_holdout(
            tmp_path / "prices",
            tmp_path / "external",
            promotion,
        )


def test_report_is_paper_only_by_default() -> None:
    fields = v16.FundingPromotionReport.__dataclass_fields__
    assert fields["paper_only"].default is True
    assert fields["authorizes_real_trading"].default is False
