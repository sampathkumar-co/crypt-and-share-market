from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest

from tradebot.research import yield_bearing_cash_v44 as model
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
            returns.append(0.01 if asset == "BTC" else 0.0)
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


def history(*values: tuple[str, float]) -> model.CashRateHistory:
    rates = {
        datetime.fromisoformat(day).replace(tzinfo=timezone.utc): rate
        for day, rate in values
    }
    dates = sorted(rates)
    return model.CashRateHistory(
        annual_rates=rates,
        source={
            "provider": model.CASH_PROVIDER,
            "series": model.CASH_SERIES,
            "observation_count": len(rates),
            "first_date": dates[0].date().isoformat(),
            "last_date": dates[-1].date().isoformat(),
        },
    )


def fixed_decisions(dataset: Dataset, mask: np.ndarray, *_args):
    result = {}
    for stamp in sorted({
        dataset.dates[index] for index in np.flatnonzero(mask)
    }):
        indexes = [
            index
            for index in np.flatnonzero(mask)
            if dataset.dates[index] == stamp
        ]
        btc = next(
            index for index in indexes if dataset.assets[index] == "BTC"
        )
        result[stamp] = {
            "regime": 0,
            "selected": [btc],
            "candidate_count": 1,
            "panic_probability": 0.0,
        }
    return result


def test_parse_cash_rates_accepts_fred_columns_and_percent_values():
    content = (
        b"observation_date,DGS3MO\n"
        b"2022-01-03,0.08\n"
        b"2022-01-04,.\n"
        b"2022-01-05,0.10\n"
    )
    rates = model.parse_cash_rates(content)
    assert rates[model.v43.day("2022-01-03")] == pytest.approx(0.0008)
    assert rates[model.v43.day("2022-01-05")] == pytest.approx(0.0010)
    assert len(rates) == 2


def test_prior_known_rate_never_uses_same_day_observation():
    rates = history(
        ("2025-01-01", 0.04),
        ("2025-01-02", 0.05),
    )
    rate_date, annual = model.prior_known_annual_rate(
        rates,
        model.v43.day("2025-01-02"),
    )
    assert rate_date == model.v43.day("2025-01-01")
    assert annual == pytest.approx(0.04)


def test_prior_known_rate_fails_closed_without_history():
    rates = history(("2025-01-02", 0.05))
    with pytest.raises(
        model.YieldBearingCashV44Error,
        match="No cash rate known",
    ):
        model.prior_known_annual_rate(
            rates,
            model.v43.day("2025-01-02"),
        )


def test_annual_to_daily_rate_compounds_back_to_annual():
    daily = model.annual_to_daily_rate(0.05)
    assert (1.0 + daily) ** 365 - 1.0 == pytest.approx(0.05)


def test_idle_cash_receives_yield_without_creating_actions(monkeypatch):
    dataset = manual_dataset(model.v43.day("2025-01-02"), 3)
    mask = np.ones(len(dataset.X), dtype=bool)

    def no_asset_decisions(dataset, mask, *_args):
        return {
            stamp: {
                "regime": 0,
                "selected": [],
                "candidate_count": 0,
                "panic_probability": 0.0,
            }
            for stamp in sorted({
                dataset.dates[index] for index in np.flatnonzero(mask)
            })
        }

    monkeypatch.setattr(model.v43, "decisions_by_date", no_asset_decisions)
    rates = history(("2025-01-01", 0.05))
    summary = model.simulate(
        dataset,
        mask,
        object(),
        {},
        rates,
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    expected = (1.0 + model.annual_to_daily_rate(0.05)) ** 3 - 1.0
    assert summary["net_return"] == pytest.approx(expected)
    assert summary["cash_contribution"] == pytest.approx(expected)
    assert summary["target_changing_actions"] == 0
    assert summary["selected_assets"] == []


def test_cash_overlay_preserves_v43_actions_and_selected_assets(monkeypatch):
    dataset = manual_dataset(model.v43.day("2025-01-02"), 6)
    mask = np.ones(len(dataset.X), dtype=bool)
    monkeypatch.setattr(model.v43, "decisions_by_date", fixed_decisions)
    baseline = model.v43.simulate(
        dataset,
        mask,
        object(),
        {},
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    overlay = model.simulate(
        dataset,
        mask,
        object(),
        {},
        history(("2025-01-01", 0.05)),
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    assert overlay["target_changing_actions"] == baseline[
        "target_changing_actions"
    ]
    assert overlay["selected_assets"] == baseline["selected_assets"]
    assert overlay["net_return"] > baseline["net_return"]
    assert overlay["maximum_target_exposure"] <= 0.0500001


def test_cash_source_query_covers_dataset_history():
    query = parse_qs(urlparse(model.FRED_CASH_URL).query)
    assert query["id"] == ["DGS3MO"]
    assert query["cosd"] == ["2022-01-01"]
    assert query["coed"] == ["2026-06-30"]
