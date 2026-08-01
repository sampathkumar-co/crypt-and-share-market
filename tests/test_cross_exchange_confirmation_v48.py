from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest

from tradebot.research import cross_exchange_confirmation_v48 as model
from tradebot.research.regime_ranking_v42 import Dataset
from tradebot.research.regime_ranking_v42_sources import (
    DailyAssetState,
    DailyBar,
)


def synthetic_states(
    start: datetime,
    day_count: int,
) -> dict[str, dict[datetime, DailyAssetState]]:
    result = {asset: {} for asset in model.ASSETS}
    for offset in range(day_count):
        stamp = start + timedelta(days=offset)
        for asset_index, asset in enumerate(model.ASSETS):
            close = 100.0 * (asset_index + 1) * (1.0 + 0.002 * offset)
            spot = DailyBar(
                day=stamp,
                open=close * 0.999,
                high=close * 1.01,
                low=close * 0.99,
                close=close,
                quote_volume=1_000_000.0 * (asset_index + 1) * (offset + 1),
                taker_buy_quote_volume=500_000.0 * (asset_index + 1) * (offset + 1),
            )
            perp = DailyBar(
                day=stamp,
                open=close,
                high=close * 1.011,
                low=close * 0.991,
                close=close * 1.001,
                quote_volume=2_000_000.0 * (asset_index + 1) * (offset + 1),
                taker_buy_quote_volume=1_050_000.0 * (asset_index + 1) * (offset + 1),
            )
            result[asset][stamp] = DailyAssetState(
                day=stamp,
                spot=spot,
                perp=perp,
                funding=0.0001,
                open_interest=10_000.0 * (offset + 1),
                basis=perp.close / spot.close - 1.0,
                spot_flow=0.0,
                perp_flow=0.05,
            )
    return result


def synthetic_coinbase(
    start: datetime,
    day_count: int,
) -> model.CoinbaseHistory:
    bars = {asset: {} for asset in model.COINBASE_PRODUCTS}
    for offset in range(day_count):
        stamp = start + timedelta(days=offset)
        for asset_index, asset in enumerate(model.COINBASE_PRODUCTS):
            close = 100.5 * (asset_index + 1) * (1.0 + 0.0022 * offset)
            bars[asset][stamp] = SimpleNamespace(
                close=close,
                quote_volume=700_000.0 * (asset_index + 1) * (offset + 1),
            )
    return model.CoinbaseHistory(
        bars=bars,
        source={"provider": "synthetic"},
    )


def manual_dataset(
    dates: list[datetime],
) -> Dataset:
    row_dates: list[datetime] = []
    assets: list[str] = []
    for stamp in dates:
        for asset in model.ASSETS:
            row_dates.append(stamp)
            assets.append(asset)
    size = len(row_dates)
    return Dataset(
        X=np.zeros((size, 3), dtype=float),
        return1=np.zeros(size),
        return3=np.zeros(size),
        return7=np.zeros(size),
        rank3=np.zeros(size),
        meta=np.ones(size, dtype=int),
        downside3=np.zeros(size, dtype=int),
        regimes=np.zeros(size, dtype=int),
        dates=row_dates,
        assets=assets,
        feature_names=["a", "b", "c"],
    )


def result(
    candidate: float,
    control: float = 0.01,
    *,
    drawdown: float = 0.01,
) -> dict[str, object]:
    common = {
        "maximum_drawdown": drawdown,
        "maximum_target_exposure": 0.10,
    }
    return {
        "candidate_standard": {"net_return": candidate, **common},
        "candidate_stress": {"net_return": candidate - 0.001, **common},
        "control_standard": {"net_return": control, **common},
        "control_stress": {"net_return": control - 0.001, **common},
    }


def test_request_ranges_are_contiguous_bounded_and_complete():
    ranges = model.request_ranges()
    assert ranges[0][0] == model.COINBASE_START
    assert ranges[-1][1] == model.COINBASE_END
    assert all(
        (end - start).days + 1 <= model.COINBASE_CHUNK_DAYS
        for start, end in ranges
    )
    assert all(
        right[0] == left[1] + timedelta(days=1)
        for left, right in zip(ranges, ranges[1:], strict=False)
    )
    covered = sum((end - start).days + 1 for start, end in ranges)
    assert covered == len(model.required_dates())


def test_candle_url_is_daily_and_end_exclusive():
    start = model.v43.day("2025-01-01")
    end = model.v43.day("2025-01-10")
    query = parse_qs(urlparse(model.candle_url("BTC-USD", start, end)).query)
    assert query["granularity"] == ["86400"]
    assert query["start"] == ["2025-01-01T00:00:00Z"]
    assert query["end"] == ["2025-01-11T00:00:00Z"]


def test_download_fails_closed_when_required_candle_is_missing(monkeypatch):
    stamp = model.v43.day("2025-01-01")
    monkeypatch.setattr(model, "request_ranges", lambda: [(stamp, stamp)])
    monkeypatch.setattr(model, "required_dates", lambda: [stamp])

    def downloader(_url: str):
        raw = json.dumps([]).encode("utf-8")
        return raw, hashlib.sha256(raw).hexdigest()

    with pytest.raises(
        model.CrossExchangeConfirmationV48Error,
        match="missing 1 required candles",
    ):
        model.download_coinbase_history(
            downloader=downloader,
            sleeper=lambda _seconds: None,
        )


def test_feature_families_are_fixed_distinct_and_combined():
    price = model.family_feature_names("price")
    liquidity = model.family_feature_names("liquidity")
    combined = model.family_feature_names("combined")
    assert len(price) == 22
    assert len(liquidity) == 17
    assert combined == [*price, *liquidity]
    assert len(set(combined)) == 39
    assert model.family_feature_names("baseline") == []


def test_future_coinbase_candle_cannot_change_prior_features():
    start = model.v43.day("2025-01-01")
    states = synthetic_states(start, 45)
    history = synthetic_coinbase(start, 45)
    target = start + timedelta(days=40)
    before = model.date_feature_map(
        states,
        history,
        "combined",
    )[target]
    future = target + timedelta(days=1)
    history.bars["BTC"][future].close *= 50.0
    history.bars["BTC"][future].quote_volume *= 50.0
    after = model.date_feature_map(
        states,
        history,
        "combined",
    )[target]
    assert after == pytest.approx(before)


def test_augmented_features_repeat_across_assets_and_preserve_labels():
    start = model.v43.day("2025-01-01")
    states = synthetic_states(start, 45)
    history = synthetic_coinbase(start, 45)
    selected_dates = [
        start + timedelta(days=40),
        start + timedelta(days=41),
    ]
    base = manual_dataset(selected_dates)
    augmented = model.augmented_dataset(
        base,
        states,
        history,
        "combined",
    )
    assert augmented.X.shape == (
        len(base.X),
        len(base.feature_names) + 39,
    )
    assert augmented.feature_names[:3] == base.feature_names
    assert np.array_equal(augmented.return1, base.return1)
    assert np.array_equal(augmented.regimes, base.regimes)
    for offset in range(0, len(base.X), len(model.ASSETS)):
        block = augmented.X[
            offset:offset + len(model.ASSETS),
            len(base.feature_names):,
        ]
        assert np.allclose(block, block[0])


def test_candidate_eligibility_requires_four_broad_improvements():
    passing = [
        result(0.012 if index < 4 else 0.009)
        for index in range(6)
    ]
    eligible, reasons = model.candidate_eligibility(passing)
    assert eligible is True
    assert reasons == []

    failing = [result(0.009) for _ in range(6)]
    eligible, reasons = model.candidate_eligibility(failing)
    assert eligible is False
    assert "fewer_than_four_positive_standard_excess_folds" in reasons
    assert "non_positive_compounded_standard_excess" in reasons


def test_selection_key_prefers_fewer_features_after_equal_performance():
    folds = [result(0.012) for _ in range(6)]
    price = model.selection_key(folds, "price")
    combined = model.selection_key(folds, "combined")
    assert price > combined
