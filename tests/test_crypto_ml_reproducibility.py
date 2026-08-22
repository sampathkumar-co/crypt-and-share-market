from __future__ import annotations

import json
from datetime import datetime, timedelta

from tradebot.ml.crypto_signal_model import (
    MODEL_VERSION,
    build_samples_from_candles,
    train_model,
)
from tradebot.models import Candle


def _candles(count: int = 80) -> list[Candle]:
    start = datetime(2025, 1, 1)
    price = 100.0
    candles: list[Candle] = []
    for index in range(count):
        open_price = price
        drift = 0.004 if index % 9 else -0.002
        close = open_price * (1.0 + drift)
        high = max(open_price, close) * (1.05 if index % 11 == 0 else 1.012)
        low = min(open_price, close) * (0.97 if index % 13 == 0 else 0.99)
        candles.append(
            Candle(
                start + timedelta(days=index),
                open_price,
                high,
                low,
                close,
                5_000.0 + index * 100.0,
            )
        )
        price = close
    return candles


def test_same_seed_produces_identical_model_and_record(tmp_path) -> None:
    samples = build_samples_from_candles("BTCUSDT", _candles())
    first = train_model(samples, random_state=2026)
    second = train_model(samples, random_state=2026)

    assert first.weights == second.weights
    assert first.bias == second.bias
    assert first.training_metadata == second.training_metadata
    assert first.training_metrics == second.training_metrics
    assert first.random_state == 2026
    assert first.model_version == MODEL_VERSION
    assert first.training_metadata["feature_set"] == first.feature_names
    assert first.training_metadata["feature_set_sha256"]

    first_path = first.save(tmp_path / "first.json")
    second_path = second.save(tmp_path / "second.json")
    assert first_path.read_bytes() == second_path.read_bytes()

    payload = json.loads(first_path.read_text(encoding="utf-8"))
    assert payload["random_state"] == 2026
    assert payload["training_metrics"]["samples"] == len(samples)
