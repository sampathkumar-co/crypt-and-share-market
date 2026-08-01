from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest

from tradebot.research import macro_liquidity_state_v47 as model
from tradebot.research.regime_ranking_v42 import Dataset


def day(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def macro_history(start: datetime, count: int = 140) -> model.MacroHistory:
    series = {}
    for offset, name in enumerate(model.SERIES_META):
        values = {
            start + timedelta(days=index): float(10 + offset + index / 10)
            for index in range(count)
        }
        dates = sorted(values)
        series[name] = model.MacroSeries(
            values=values,
            source={
                "series": name,
                "first_date": dates[0].date().isoformat(),
                "last_date": dates[-1].date().isoformat(),
                "observation_count": len(values),
            },
        )
    return model.MacroHistory(series)


def manual_dataset(start: datetime, day_count: int) -> Dataset:
    dates: list[datetime] = []
    assets: list[str] = []
    returns1: list[float] = []
    returns3: list[float] = []
    for offset in range(day_count):
        stamp = start + timedelta(days=offset)
        for index, asset in enumerate(model.ASSETS):
            dates.append(stamp)
            assets.append(asset)
            returns1.append(0.001 * (index + 1))
            returns3.append(0.01 if offset % 2 == 0 else -0.01)
    size = len(dates)
    return Dataset(
        X=np.zeros((size, 2), dtype=float),
        return1=np.asarray(returns1),
        return3=np.asarray(returns3),
        return7=np.asarray(returns3),
        rank3=np.zeros(size),
        meta=np.ones(size, dtype=int),
        downside3=np.zeros(size, dtype=int),
        regimes=np.zeros(size, dtype=int),
        dates=dates,
        assets=assets,
        feature_names=["x", "y"],
    )


def summary(
    net_return: float = 0.0,
    *,
    gated: int = 0,
    actions: int = 1,
    turnover: float = 0.1,
    drawdown: float = 0.01,
) -> dict[str, float | int | list[str] | dict[str, float] | bool]:
    return {
        "net_return": net_return,
        "maximum_drawdown": drawdown,
        "turnover": turnover,
        "target_changing_actions": actions,
        "selected_assets": ["BTC"],
        "gated_assets": [],
        "gated_decision_count": gated,
        "asset_contribution": {asset: 0.0 for asset in model.ASSETS},
        "regime_contribution": {
            name: 0.0 for name in model.REGIME_NAMES.values()
        },
        "cash_contribution": 0.0,
        "decision_count": 1,
        "maximum_gross_exposure": 0.05,
        "maximum_target_exposure": 0.05,
        "maximum_selected_cardinality": 1,
        "never_added_asset": True,
    }


def test_parse_fred_series_accepts_observation_date_and_skips_missing():
    content = (
        b"observation_date,VIXCLS\n"
        b"2025-01-01,15.25\n"
        b"2025-01-02,.\n"
        b"2025-01-03,16.50\n"
    )
    values = model.parse_fred_series(content, "VIXCLS")
    assert values[day("2025-01-01")] == pytest.approx(15.25)
    assert values[day("2025-01-03")] == pytest.approx(16.50)
    assert len(values) == 2


def test_prior_known_value_never_uses_same_day_observation():
    history = macro_history(day("2025-01-01"), 10)
    used, value = model.prior_known_value(
        history,
        "VIXCLS",
        day("2025-01-03"),
    )
    assert used == day("2025-01-02")
    assert value == pytest.approx(10.1)


def test_macro_feature_vector_is_finite_and_fixed_width():
    history = macro_history(day("2024-01-01"), 160)
    vector = model.macro_feature_vector(
        history,
        day("2024-05-20"),
    )
    assert vector.shape == (len(model.MACRO_FEATURE_NAMES),)
    assert np.all(np.isfinite(vector))


def test_macro_matrix_repeats_identical_date_features_across_assets():
    start = day("2024-04-01")
    dataset = manual_dataset(start, 3)
    history = macro_history(day("2024-01-01"), 180)
    matrix, by_date = model.build_macro_matrix(dataset, history)
    assert matrix.shape == (len(dataset.X), len(model.MACRO_FEATURE_NAMES))
    for offset in range(3):
        rows = matrix[offset * len(model.ASSETS):(offset + 1) * len(model.ASSETS)]
        assert np.allclose(rows, rows[0])
        assert np.allclose(rows[0], by_date[start + timedelta(days=offset)])


def test_date_level_samples_deduplicate_asset_rows():
    start = day("2024-04-01")
    dataset = manual_dataset(start, 130)
    history = macro_history(day("2024-01-01"), 300)
    _, by_date = model.build_macro_matrix(dataset, history)
    X, y, dates = model.date_level_samples(
        dataset,
        by_date,
        "risk_appetite",
        start=None,
        end=start + timedelta(days=129),
    )
    assert len(X) == 130
    assert len(y) == 130
    assert len(dates) == 130
    assert X.shape[1] == len(model.FAMILY_COLUMNS["risk_appetite"])


def test_macro_gate_can_only_remove_baseline_assets(monkeypatch):
    dataset = manual_dataset(day("2025-01-01"), 1)
    mask = np.ones(len(dataset.X), dtype=bool)

    def fixed_decisions(dataset, mask, *_args):
        return {
            day("2025-01-01"): {
                "regime": 0,
                "selected": [0],
                "candidate_count": 1,
                "panic_probability": 0.0,
            }
        }

    monkeypatch.setattr(model.v43, "decisions_by_date", fixed_decisions)
    gated = model.macro_gated_decisions(
        dataset,
        mask,
        object(),
        {},
        {day("2025-01-01"): 0.20},
        0.50,
    )
    decision = gated[day("2025-01-01")]
    assert decision["selected"] == []
    assert decision["gated_assets"] == ["BTC"]


def test_macro_gate_preserves_panic_to_cash(monkeypatch):
    dataset = manual_dataset(day("2025-01-01"), 1)
    mask = np.ones(len(dataset.X), dtype=bool)

    def panic_decisions(dataset, mask, *_args):
        return {
            day("2025-01-01"): {
                "regime": 2,
                "selected": [],
                "candidate_count": 0,
                "panic_probability": 0.8,
            }
        }

    monkeypatch.setattr(model.v43, "decisions_by_date", panic_decisions)
    gated = model.macro_gated_decisions(
        dataset,
        mask,
        object(),
        {},
        {day("2025-01-01"): 0.99},
        0.50,
    )
    assert gated[day("2025-01-01")]["selected"] == []
    assert gated[day("2025-01-01")]["gated_assets"] == []


def test_family_selection_falls_back_when_active_family_has_no_intervention():
    results = []
    for index in range(6):
        base = summary(0.01)
        gated = summary(0.01, gated=0)
        results.append(model.FamilyFoldResult(
            fold=f"WF-{index + 1}",
            family="risk_appetite",
            threshold=None,
            training_date_count=200,
            positive_label_share=0.5,
            calibration_baseline=base,
            calibration_gated=gated,
            calibration_excess=0.0,
            validation_baseline=base,
            validation_gated=gated,
            validation_excess=0.0,
        ))
    selected, report = model.select_macro_family({
        "risk_appetite": results,
    })
    assert selected == "disabled"
    assert report["selected_is_disabled_baseline"] is True


def test_fred_urls_use_fixed_series_and_date_range():
    assert set(model.FRED_URLS) == set(model.SERIES_META)
    for series, url in model.FRED_URLS.items():
        query = parse_qs(urlparse(url).query)
        assert query["id"] == [series]
        assert query["cosd"] == ["2022-01-01"]
        assert query["coed"] == ["2026-06-30"]
