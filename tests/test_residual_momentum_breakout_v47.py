from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from tradebot.research import residual_momentum_breakout_v47 as model


def manual_dataset(day_count: int = 4) -> model.ResidualDataset:
    dates = [
        datetime(2025, 7, 1, tzinfo=timezone.utc) + timedelta(days=i)
        for i in range(day_count)
    ]
    shape = (day_count, len(model.ASSETS))
    positive = np.full(shape, 0.05, dtype=float)
    ranks = np.tile(
        np.asarray([1.0, 0.75, 0.50, 0.25, 0.0]),
        (day_count, 1),
    )
    return model.ResidualDataset(
        dates=dates,
        return1=np.tile(
            np.asarray([0.01, 0.005, 0.0, -0.002, -0.003]),
            (day_count, 1),
        ),
        residual20=positive.copy(),
        residual60=positive.copy(),
        residual_score=np.tile(
            np.asarray([2.0, 1.5, 1.0, 0.5, 0.0]),
            (day_count, 1),
        ),
        residual_rank=ranks,
        return7=positive.copy(),
        return20=positive.copy(),
        return60=positive.copy(),
        return120=positive.copy(),
        sma20_distance=positive.copy(),
        sma50_distance=positive.copy(),
        efficiency20=np.full(shape, 0.6),
        compression_ratio=np.full(shape, 0.5),
        breakout_distance20=np.full(shape, 0.02),
        volume_ratio20=np.full(shape, 1.2),
        btc_above_sma100=np.ones(day_count),
        breadth50=np.ones(day_count),
        observable_regime=np.ones(day_count, dtype=int),
    )


def config(**updates) -> model.Config:
    values = {
        "residual_floor": 0.02,
        "rank_floor": 0.60,
        "efficiency_floor": 0.35,
        "compression_ceiling": 0.80,
        "breakout_buffer": 0.01,
        "entry_mode": "either",
    }
    values.update(updates)
    return model.Config(**values)


def cash_history(start: datetime) -> model.v44.CashRateHistory:
    prior = start - timedelta(days=1)
    return model.v44.CashRateHistory(
        annual_rates={prior: 0.05},
        source={
            "provider": model.v44.CASH_PROVIDER,
            "series": model.v44.CASH_SERIES,
            "observation_count": 1,
            "first_date": prior.date().isoformat(),
            "last_date": prior.date().isoformat(),
        },
    )


def test_grid_is_exactly_protocol_size():
    grid = model.config_grid()
    assert len(grid) == 324
    assert len(set(grid)) == 324


def test_stable_percentile_is_deterministic_for_ties():
    values = np.asarray([2.0, 2.0, 1.0, 3.0])
    first = model.stable_percentile(values)
    second = model.stable_percentile(values)
    assert np.array_equal(first, second)
    assert first[0] < first[1]
    assert first[3] == 1.0


def test_beta_is_zero_for_constant_factor():
    asset = np.asarray([0.1, 0.2, -0.1])
    factor = np.asarray([0.0, 0.0, 0.0])
    assert model.beta60(asset, factor) == 0.0


def test_market_gate_accepts_btc_or_breadth():
    dataset = manual_dataset(1)
    dataset.btc_above_sma100[0] = 0.0
    dataset.breadth50[0] = 0.60
    assert model.market_risk_on(dataset, 0) is True
    dataset.breadth50[0] = 0.40
    assert model.market_risk_on(dataset, 0) is False


def test_either_mode_prefers_breakout_label():
    dataset = manual_dataset(1)
    qualifies, signal = model.qualification(dataset, 0, 0, config())
    assert qualifies is True
    assert signal == "breakout"


def test_continuation_rejects_low_efficiency():
    dataset = manual_dataset(1)
    dataset.efficiency20[0, 0] = 0.10
    qualifies, signal = model.qualification(
        dataset,
        0,
        0,
        config(entry_mode="continuation"),
    )
    assert qualifies is False
    assert signal is None


def test_decision_selects_only_top_residual_asset():
    dataset = manual_dataset(1)
    decision = model.decisions_by_date(
        dataset,
        np.ones(1, dtype=bool),
        config(),
    )[dataset.dates[0]]
    assert decision["selected"] == [0]
    assert decision["signal"] == "breakout"


def test_simulation_keeps_five_percent_exposure_and_earns_cash():
    dataset = manual_dataset(5)
    result = model.simulate(
        dataset,
        np.ones(5, dtype=bool),
        cash_history(dataset.dates[0]),
        config(),
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    assert result["maximum_target_exposure"] <= 0.0500001
    assert result["maximum_gross_exposure"] <= 0.051
    assert result["selected_assets"] == ["BTC"]
    assert result["cash_contribution"] > 0.0
    assert result["target_changing_actions"] >= 1


def test_candidate_eligibility_requires_broad_positive_folds():
    def summary(value: float, assets: list[str]) -> dict:
        return {
            "net_return": value,
            "maximum_drawdown": 0.01,
            "turnover": 0.2,
            "target_changing_actions": 4,
            "selected_assets": assets,
            "asset_contribution": {
                "BTC": 0.004,
                "ETH": 0.003,
                "SOL": 0.002,
                "XRP": 0.001,
                "ADA": 0.0,
            },
        }

    folds = [
        {
            "standard": summary(0.01, ["BTC", "ETH", "SOL"]),
            "stress": summary(0.008, ["BTC", "ETH", "SOL"]),
        }
        for _ in range(6)
    ]
    diagnostic = model.candidate_diagnostics(folds)
    assert diagnostic["eligible"] is True
    for position in range(3):
        folds[position]["standard"]["net_return"] = -0.20
        folds[position]["stress"]["net_return"] = -0.20
    assert model.candidate_diagnostics(folds)["eligible"] is False


def test_selection_folds_end_before_sealed_period():
    assert model.FOLD_WINDOWS[-1][2] < model.v43.SEALED_WINDOWS[0][1]
    for _, start, end in model.FOLD_WINDOWS:
        assert start <= end


def test_source_keeps_paper_only_boundary():
    text = Path(model.__file__).read_text(encoding="utf-8").lower()
    assert "private_key" not in text
    assert "create_order" not in text
    assert "place_order" not in text
    assert '"authorizes_trading": false' in text
