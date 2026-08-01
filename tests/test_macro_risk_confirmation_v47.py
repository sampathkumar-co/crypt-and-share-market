from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest

from tradebot.research import macro_risk_confirmation_v47 as model
from tradebot.research.regime_ranking_v42 import Dataset


def manual_dataset(start: datetime, day_count: int) -> Dataset:
    dates: list[datetime] = []
    assets: list[str] = []
    returns: list[float] = []
    for offset in range(day_count):
        stamp = start + timedelta(days=offset)
        for asset in model.ASSETS:
            dates.append(stamp)
            assets.append(asset)
            returns.append(
                0.01 if asset == "BTC"
                else 0.005 if asset == "ETH"
                else 0.0
            )
    size = len(dates)
    return Dataset(
        X=np.zeros((size, 2), dtype=float),
        return1=np.asarray(returns),
        return3=np.zeros(size),
        return7=np.zeros(size),
        rank3=np.zeros(size),
        meta=np.ones(size, dtype=int),
        downside3=np.zeros(size, dtype=int),
        regimes=np.zeros(size, dtype=int),
        dates=dates,
        assets=assets,
        feature_names=["x", "y"],
    )


def cash_history(start: datetime) -> model.v44.CashRateHistory:
    rates = {
        start - timedelta(days=1): 0.05,
    }
    return model.v44.CashRateHistory(
        annual_rates=rates,
        source={
            "provider": model.v44.CASH_PROVIDER,
            "series": model.v44.CASH_SERIES,
            "observation_count": 1,
            "first_date": min(rates).date().isoformat(),
            "last_date": max(rates).date().isoformat(),
        },
    )


def synthetic_macro_history(
    start: datetime,
    count: int = 500,
) -> model.MacroHistory:
    values = {
        "VIXCLS": {},
        "DTWEXBGS": {},
        "DFII10": {},
    }
    for index in range(count):
        stamp = start + timedelta(days=index)
        values["VIXCLS"][stamp] = 15.0 + (index % 30) * 0.2
        values["DTWEXBGS"][stamp] = 100.0 + index * 0.01
        values["DFII10"][stamp] = 1.0 + (index % 50) * 0.01
    return model.MacroHistory(
        values=values,
        source={"provider": model.MACRO_PROVIDER},
    )


def fixed_decisions(
    dataset: Dataset,
    mask: np.ndarray,
    *_args,
    two_assets: bool = False,
):
    result = {}
    for stamp in sorted({
        dataset.dates[index]
        for index in np.flatnonzero(mask)
    }):
        indexes = [
            index
            for index in np.flatnonzero(mask)
            if dataset.dates[index] == stamp
        ]
        btc = next(
            index for index in indexes
            if dataset.assets[index] == "BTC"
        )
        selected = [btc]
        if two_assets:
            eth = next(
                index for index in indexes
                if dataset.assets[index] == "ETH"
            )
            selected.append(eth)
        result[stamp] = {
            "regime": 0,
            "selected": selected,
            "candidate_count": len(selected),
            "panic_probability": 0.0,
        }
    return result


def snapshots_for(
    dataset: Dataset,
    score: float,
) -> dict[datetime, model.MacroSnapshot]:
    return {
        stamp: model.MacroSnapshot(
            score=score,
            components={
                "vix_level_percentile": score,
                "dollar_return_60_percentile": score,
                "real_yield_change_20_percentile": score,
            },
            asof_dates={
                series: stamp - timedelta(days=1)
                for series in model.MACRO_SERIES
            },
            raw_values={series: 1.0 for series in model.MACRO_SERIES},
        )
        for stamp in set(dataset.dates)
    }


def summary(
    net_return: float,
    *,
    drawdown: float = 0.01,
    exposure: float = 0.10,
) -> dict[str, float | int]:
    return {
        "net_return": net_return,
        "maximum_drawdown": drawdown,
        "maximum_target_exposure": exposure,
        "selected_identity_mismatches": 0,
    }


def test_parse_fred_series_accepts_standard_columns_and_missing_values():
    content = (
        b"observation_date,VIXCLS\n"
        b"2024-01-02,13.20\n"
        b"2024-01-03,.\n"
        b"2024-01-04,14.50\n"
    )
    values = model.parse_fred_series(content, "VIXCLS")
    assert values[model.v43.day("2024-01-02")] == pytest.approx(13.2)
    assert values[model.v43.day("2024-01-04")] == pytest.approx(14.5)
    assert len(values) == 2


def test_asof_index_never_uses_same_day_observation():
    history = model.MacroHistory(
        values={
            series: {
                model.v43.day("2025-01-01"): 1.0,
                model.v43.day("2025-01-02"): 2.0,
            }
            for series in model.MACRO_SERIES
        },
        source={},
    )
    dates, index = model._asof_index(
        history,
        "VIXCLS",
        model.v43.day("2025-01-02"),
    )
    assert dates[index] == model.v43.day("2025-01-01")


def test_asof_index_fails_closed_for_stale_macro_data():
    history = model.MacroHistory(
        values={
            series: {model.v43.day("2025-01-01"): 1.0}
            for series in model.MACRO_SERIES
        },
        source={},
    )
    with pytest.raises(
        model.MacroRiskConfirmationV47Error,
        match="stale VIXCLS",
    ):
        model._asof_index(
            history,
            "VIXCLS",
            model.v43.day("2025-01-10"),
        )


def test_macro_snapshot_percentiles_and_score_are_bounded():
    history = synthetic_macro_history(model.v43.day("2023-01-01"))
    snapshot = model.macro_snapshot(
        history,
        model.v43.day("2024-05-01"),
    )
    assert 0.0 <= snapshot.score <= 1.0
    assert all(
        0.0 <= value <= 1.0
        for value in snapshot.components.values()
    )
    assert all(
        day <= model.v43.day("2024-04-30")
        for day in snapshot.asof_dates.values()
    )


def test_macro_grid_is_fixed_unique_and_exposure_bounded():
    grid = model.macro_grid()
    assert len(grid) == 37
    assert len(set(grid)) == 37
    assert grid[0] == model.DISABLED_MACRO
    for config in grid:
        assert config.supportive_multiplier <= 1.5
        assert config.defensive_multiplier <= 1.0
        assert config.supportive_threshold < config.defensive_threshold


def test_disabled_macro_exactly_reproduces_v44_simulation(monkeypatch):
    start = model.v43.day("2025-01-02")
    dataset = manual_dataset(start, 9)
    mask = np.ones(len(dataset.X), dtype=bool)
    monkeypatch.setattr(
        model.v43,
        "decisions_by_date",
        lambda dataset, mask, *_args: fixed_decisions(dataset, mask),
    )
    history = cash_history(start)
    baseline = model.v44.simulate(
        dataset,
        mask,
        object(),
        {},
        history,
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    overlay = model.simulate_macro(
        dataset,
        mask,
        object(),
        {},
        history,
        snapshots_for(dataset, 0.5),
        model.DISABLED_MACRO,
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    assert overlay["net_return"] == pytest.approx(
        baseline["net_return"]
    )
    assert overlay["turnover"] == pytest.approx(
        baseline["turnover"]
    )
    assert overlay["target_changing_actions"] == baseline[
        "target_changing_actions"
    ]
    assert overlay["selected_assets"] == baseline["selected_assets"]
    assert overlay["selected_identity_mismatches"] == 0


def test_supportive_macro_scales_only_existing_assets_to_fifteen_percent(
    monkeypatch,
):
    start = model.v43.day("2025-01-02")
    dataset = manual_dataset(start, 9)
    mask = np.ones(len(dataset.X), dtype=bool)
    monkeypatch.setattr(
        model.v43,
        "decisions_by_date",
        lambda dataset, mask, *_args: fixed_decisions(
            dataset,
            mask,
            two_assets=True,
        ),
    )
    config = model.MacroConfig(0.4, 0.7, 1.5, 0.5)
    overlay = model.simulate_macro(
        dataset,
        mask,
        object(),
        {},
        cash_history(start),
        snapshots_for(dataset, 0.1),
        config,
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    assert overlay["maximum_target_exposure"] == pytest.approx(0.15)
    assert overlay["decision_selected_assets"] == ["BTC", "ETH"]
    assert overlay["selected_assets"] == ["BTC", "ETH"]
    assert overlay["selected_identity_mismatches"] == 0
    assert overlay["macro_state_counts"]["supportive"] > 0


def test_active_eligibility_requires_broad_walk_forward_improvement():
    improving = []
    for index in range(6):
        positive = index < 4
        baseline_return = 0.01
        macro_return = 0.012 if positive else 0.009
        improving.append({
            "baseline_standard": summary(baseline_return),
            "baseline_stress": summary(0.008),
            "macro_standard": summary(macro_return),
            "macro_stress": summary(
                0.010 if positive else 0.0075
            ),
        })
    eligible, reasons = model.active_eligibility(improving)
    assert eligible is True
    assert reasons == []

    failing = [
        {
            "baseline_standard": summary(0.01),
            "baseline_stress": summary(0.008),
            "macro_standard": summary(0.009),
            "macro_stress": summary(0.007),
        }
        for _ in range(6)
    ]
    eligible, reasons = model.active_eligibility(failing)
    assert eligible is False
    assert "fewer_than_four_positive_standard_excess_folds" in reasons


def test_macro_urls_use_fixed_series_and_date_range():
    for series, url in model.MACRO_URLS.items():
        query = parse_qs(urlparse(url).query)
        assert query["id"] == [series]
        assert query["cosd"] == ["2020-01-01"]
        assert query["coed"] == ["2026-06-30"]
