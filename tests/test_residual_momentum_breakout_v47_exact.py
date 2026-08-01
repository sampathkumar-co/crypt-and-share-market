from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from tradebot.research import residual_momentum_breakout_v47_exact as exact


def manual_dataset(day_count: int) -> exact.core.ResidualDataset:
    dates = [
        datetime(2025, 7, 1, tzinfo=timezone.utc) + timedelta(days=index)
        for index in range(day_count)
    ]
    shape = (day_count, len(exact.core.ASSETS))
    matrix = np.arange(day_count * len(exact.core.ASSETS), dtype=float).reshape(shape)
    return exact.core.ResidualDataset(
        dates=dates,
        return1=matrix.copy(),
        residual20=matrix.copy(),
        residual60=matrix.copy(),
        residual_score=matrix.copy(),
        residual_rank=matrix.copy(),
        return7=matrix.copy(),
        return20=matrix.copy(),
        return60=matrix.copy(),
        return120=matrix.copy(),
        sma20_distance=matrix.copy(),
        sma50_distance=matrix.copy(),
        efficiency20=matrix.copy(),
        compression_ratio=matrix.copy(),
        breakout_distance20=matrix.copy(),
        volume_ratio20=matrix.copy(),
        btc_above_sma100=np.arange(day_count, dtype=float),
        breadth50=np.arange(day_count, dtype=float),
        observable_regime=np.arange(day_count, dtype=int),
    )


def test_filter_to_dates_slices_every_series_consistently():
    dataset = manual_dataset(5)
    allowed = {dataset.dates[0], dataset.dates[2], dataset.dates[4]}
    filtered = exact.filter_to_dates(dataset, allowed)
    assert filtered.dates == [dataset.dates[0], dataset.dates[2], dataset.dates[4]]
    assert filtered.return1.shape == (3, len(exact.core.ASSETS))
    assert np.array_equal(filtered.return1[1], dataset.return1[2])
    assert len(filtered.btc_above_sma100) == 3


def test_filter_removes_dates_not_in_frozen_reference():
    dataset = manual_dataset(4)
    allowed = set(dataset.dates[:-1])
    filtered = exact.filter_to_dates(dataset, allowed)
    assert filtered.dates[-1] == dataset.dates[-2]
    assert dataset.dates[-1] - filtered.dates[-1] == timedelta(days=1)
