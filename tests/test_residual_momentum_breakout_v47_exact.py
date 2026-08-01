from __future__ import annotations

from datetime import timedelta

import numpy as np

from tradebot.research import residual_momentum_breakout_v47_exact as exact
from tests.test_residual_momentum_breakout_v47 import manual_dataset


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
