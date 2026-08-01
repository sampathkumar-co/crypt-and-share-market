from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone

import numpy as np
import pytest

from tradebot.research.historical_proxy_screen_v25 import DownloadedArchive
from tradebot.research import regime_ranking_v42_sources as sources


def archive(name: str, csv_text: str) -> DownloadedArchive:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr(name, csv_text)
    content = buffer.getvalue()
    return DownloadedArchive("https://example.test/" + name, "sha", content)


def test_parse_daily_kline_and_flow():
    item = archive(
        "bars.csv",
        "1609459200000,100,110,90,105,0,0,1000,0,0,600\n",
    )
    bars = sources.parse_daily_klines(item)
    day = datetime(2021, 1, 1, tzinfo=timezone.utc)
    assert bars[day].close == 105.0
    assert sources.flow_imbalance(bars[day]) == pytest.approx(0.2)


def test_funding_sums_by_utc_day():
    item = archive(
        "funding.csv",
        "calc_time,symbol,last_funding_rate\n"
        "2021-01-01 00:00:00,BTCUSDT,0.0001\n"
        "2021-01-01 08:00:00,BTCUSDT,-0.0002\n"
        "2021-01-02 00:00:00,BTCUSDT,0.0003\n",
    )
    values = sources.aggregate_daily_funding(item)
    first = datetime(2021, 1, 1, tzinfo=timezone.utc)
    second = datetime(2021, 1, 2, tzinfo=timezone.utc)
    assert values[first] == pytest.approx(-0.0001)
    assert values[second] == pytest.approx(0.0003)


def test_open_interest_uses_last_positive_observation():
    item = archive(
        "metrics.csv",
        "create_time,sum_open_interest\n"
        "2021-01-01 00:05:00,10\n"
        "2021-01-01 12:00:00,0\n"
        "2021-01-01 23:55:00,15\n",
    )
    assert sources.parse_daily_open_interest(item) == 15.0


def test_assemble_states_closes_missing_asset_day(monkeypatch):
    day = datetime(2021, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(sources, "days", lambda *args, **kwargs: ["2021-01-01"])
    bar = sources.DailyBar(day, 100, 110, 90, 105, 1000, 600)
    spot = {asset: {day: bar} for asset in sources.ASSETS}
    perp = {asset: {day: bar} for asset in sources.ASSETS}
    funding = {asset: {day: 0.0} for asset in sources.ASSETS}
    open_interest = {asset: {day: 10.0} for asset in sources.ASSETS}
    del open_interest["ADA"][day]

    states, missing = sources.assemble_states(spot, perp, funding, open_interest)
    assert day in states["BTC"]
    assert day not in states["ADA"]
    assert missing == [{
        "asset": "ADA",
        "day": "2021-01-01",
        "missing": "open_interest",
    }]


def test_cached_download_remembers_static_404(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "CACHE_ROOT", tmp_path)
    calls = {"count": 0}

    def missing(*args, **kwargs):
        calls["count"] += 1
        raise sources.HTTPError("url", 404, "missing", {}, None)

    monkeypatch.setattr(sources, "urlopen", missing)
    assert sources.cached_download("https://example.test/a.zip", optional_404=True) is None
    assert sources.cached_download("https://example.test/a.zip", optional_404=True) is None
    assert calls["count"] == 1


from datetime import timedelta

from tradebot.research import regime_ranking_v42 as model


def synthetic_states(count: int = 260):
    start = datetime(2021, 1, 1, tzinfo=timezone.utc)
    result = {asset: {} for asset in sources.ASSETS}
    for index in range(count):
        stamp = start + timedelta(days=index)
        for pos, asset in enumerate(sources.ASSETS):
            trend = (1.0 + 0.0004 * (pos + 1)) ** index
            wave = 1.0 + 0.02 * np.sin(index / 17.0 + pos)
            close = 100.0 * (pos + 1) * trend * wave
            open_ = close * (1.0 - 0.001 * np.cos(index / 5.0))
            high = max(open_, close) * 1.01
            low = min(open_, close) * 0.99
            volume = 1_000_000.0 * (pos + 1) * (1.0 + index / 5000.0)
            spot = sources.DailyBar(
                stamp, open_, high, low, close, volume, volume * 0.55
            )
            perp_close = close * (1.0 + 0.001 * np.sin(index / 9.0 + pos))
            perp = sources.DailyBar(
                stamp, open_, high, low, perp_close, volume * 1.2, volume * 0.62
            )
            result[asset][stamp] = sources.DailyAssetState(
                day=stamp,
                spot=spot,
                perp=perp,
                funding=0.0001 * np.sin(index / 11.0 + pos),
                open_interest=(
                    10_000.0 * (pos + 1)
                    * (1.0 + index / 2000.0)
                    * (1.0 + 0.01 * np.cos(index / 13.0 + pos))
                ),
                basis=perp_close / close - 1.0,
                spot_flow=0.10,
                perp_flow=0.24,
            )
    return result


def test_dataset_has_exact_width_and_next_open_labels():
    states = synthetic_states()
    dataset = model.build_dataset(states)
    assert dataset.X.shape[1] == len(model.FEATURE_NAMES)
    assert len(set(dataset.dates)) == 53
    first_date = dataset.dates[0]
    asset = dataset.assets[0]
    position = sources.ASSETS.index(asset)
    dates, arrays = model.state_arrays(states)
    index = dates.index(first_date)
    expected = (
        arrays[asset]["spot_open"][index + 4]
        / arrays[asset]["spot_open"][index + 1]
        - 1.0
    )
    assert dataset.return3[0] == pytest.approx(expected)


def manual_dataset(day_count: int = 6) -> model.Dataset:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    dates: list[datetime] = []
    assets: list[str] = []
    returns: list[float] = []
    for offset in range(day_count):
        stamp = start + timedelta(days=offset)
        for asset in sources.ASSETS:
            dates.append(stamp)
            assets.append(asset)
            returns.append(0.01 if asset == "BTC" else 0.0)
    size = len(dates)
    return model.Dataset(
        X=np.zeros((size, 1), dtype=float),
        return1=np.asarray(returns),
        return3=np.zeros(size),
        return7=np.zeros(size),
        rank3=np.zeros(size),
        meta=np.ones(size, dtype=int),
        downside3=np.zeros(size, dtype=int),
        regimes=np.ones(size, dtype=int),
        dates=dates,
        assets=assets,
        feature_names=["x"],
    )


def fake_bundle(top_n: int = 1) -> model.Bundle:
    return model.Bundle(
        specialists={},
        meta_models=[],
        downside_models=[],
        regime_models=[],
        config={"learning_rate": 0.04, "max_leaf_nodes": 15, "max_iter": 120},
        meta_threshold=0.45,
        downside_limit=0.35,
        top_n=top_n,
        disagreement_threshold=0.10,
        feature_names=["x"],
    )


def fake_predictions(
    dataset: model.Dataset,
    *,
    panic_offset: int | None = None,
) -> dict[str, object]:
    size = len(dataset.dates)
    regime = np.ones(size, dtype=int)
    if panic_offset is not None:
        panic_date = sorted(set(dataset.dates))[panic_offset]
        for index, stamp in enumerate(dataset.dates):
            if stamp == panic_date:
                regime[index] = 2
    rank_map = {
        "BTC": 0.90,
        "ETH": 1.00,
        "SOL": 0.70,
        "XRP": 0.50,
        "ADA": 0.30,
    }
    rank = np.asarray([rank_map[asset] for asset in dataset.assets])
    return3 = np.asarray([
        0.03 if asset == "BTC" else 0.02
        for asset in dataset.assets
    ])
    return {
        "meta": np.full(size, 0.90),
        "meta_std": np.zeros(size),
        "downside": np.full(size, 0.10),
        "downside_std": np.zeros(size),
        "regime": regime,
        "specialists": {
            1: {
                "return3": return3,
                "return7": np.full(size, 0.02),
                "rank": rank,
                "disagreement": np.full(size, 0.01),
            }
        },
    }


def test_rank_prediction_is_primary_selection_key():
    dataset = manual_dataset(day_count=1)
    bundle = fake_bundle(top_n=1)
    predictions = fake_predictions(dataset)
    mask = np.ones(len(dataset.dates), dtype=bool)
    decisions = model.decisions_by_date(
        dataset, mask, bundle, predictions
    )
    selected = next(iter(decisions.values()))["selected"]
    assert [dataset.assets[index] for index in selected] == ["ETH"]


def test_three_day_cadence_and_terminal_liquidation_action_count():
    dataset = manual_dataset(day_count=6)
    bundle = fake_bundle(top_n=1)
    predictions = fake_predictions(dataset)
    mask = np.ones(len(dataset.dates), dtype=bool)
    summary = model.simulate(
        dataset,
        mask,
        bundle,
        predictions,
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    assert summary["target_changing_actions"] == 2
    assert summary["maximum_target_exposure"] <= 0.0500001
    assert summary["selected_assets"] == ["ETH"]


def test_panic_exits_before_scheduled_rebalance():
    dataset = manual_dataset(day_count=3)
    bundle = fake_bundle(top_n=1)
    predictions = fake_predictions(dataset, panic_offset=1)
    mask = np.ones(len(dataset.dates), dtype=bool)
    summary = model.simulate(
        dataset,
        mask,
        bundle,
        predictions,
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    assert summary["target_changing_actions"] == 2
    assert summary["terminal_equity_before_liquidation"] == pytest.approx(
        1.0 - 2.0 * 0.05 * model.STANDARD_ONE_WAY_COST,
        rel=1e-3,
    )


def test_top_two_never_exceeds_ten_percent_exposure():
    dataset = manual_dataset(day_count=2)
    bundle = fake_bundle(top_n=2)
    predictions = fake_predictions(dataset)
    mask = np.ones(len(dataset.dates), dtype=bool)
    summary = model.simulate(
        dataset,
        mask,
        bundle,
        predictions,
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    assert summary["maximum_target_exposure"] <= 0.1000001


def test_repeated_panic_while_cash_does_not_restart_clock():
    dataset = manual_dataset(day_count=4)
    bundle = fake_bundle(top_n=1)
    predictions = fake_predictions(dataset)
    panic_dates = set(sorted(set(dataset.dates))[:3])
    predictions["regime"] = np.asarray([
        2 if stamp in panic_dates else 1
        for stamp in dataset.dates
    ])
    mask = np.ones(len(dataset.dates), dtype=bool)
    summary = model.simulate(
        dataset,
        mask,
        bundle,
        predictions,
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    assert summary["target_changing_actions"] == 1
    assert summary["selected_assets"] == ["ETH"]


def test_terminal_liquidation_cost_is_in_drawdown():
    dataset = manual_dataset(day_count=1)
    bundle = fake_bundle(top_n=1)
    predictions = fake_predictions(dataset)
    mask = np.ones(len(dataset.dates), dtype=bool)
    summary = model.simulate(
        dataset,
        mask,
        bundle,
        predictions,
        one_way_cost=model.STANDARD_ONE_WAY_COST,
    )
    assert summary["net_return"] < 0.0
    assert summary["maximum_drawdown"] == pytest.approx(
        -summary["net_return"], rel=1e-6
    )


def test_bundle_state_is_portable_joblib(tmp_path):
    import joblib

    bundle = fake_bundle(top_n=2)
    bundle.specialists = {
        1: model.Specialist([], [], []),
    }
    path = tmp_path / "bundle.joblib"
    joblib.dump(model.bundle_to_state(bundle), path)
    raw = joblib.load(path)
    assert isinstance(raw, dict)
    restored = model.bundle_from_state(raw)
    assert restored.top_n == 2
    assert sorted(restored.specialists) == [1]
    assert restored.meta_threshold == pytest.approx(0.45)
