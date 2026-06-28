from __future__ import annotations

import json
from datetime import datetime, timedelta

from tradebot.ml.crypto_signal_model import (
    CryptoSignalModel,
    LabelConfig,
    MLSample,
    build_samples_from_candles,
    chronological_split,
    evaluate_model,
    extract_features,
    generate_label,
    train_model,
)
from tradebot.models import Candle, Market
from tradebot.scanner.crypto_scanner import evaluate_symbol


def make_candles(count: int = 45, jump_index: int | None = None, dump_index: int | None = None) -> list[Candle]:
    start = datetime(2025, 1, 1)
    price = 100.0
    candles: list[Candle] = []
    for i in range(count):
        open_price = price
        price += 0.5
        high = max(open_price, price) * 1.01
        low = min(open_price, price) * 0.99
        if jump_index is not None and i == jump_index:
            high = open_price * 1.08
        if dump_index is not None and i == dump_index:
            low = open_price * 0.94
        candles.append(Candle(start + timedelta(days=i), open_price, high, low, price, 5000 + i * 100))
    return candles


def test_feature_extraction_shape():
    features = extract_features(make_candles(), 25)
    assert "ma_ratio" in features
    assert "volume_ratio" in features
    assert len(features) >= 10


def test_label_generation_target_before_stop():
    candles = make_candles(jump_index=26)
    label = generate_label(candles, 25, LabelConfig(target_percent=0.04, stop_loss_percent=0.02, max_holding_bars=5))
    assert label == 1


def test_chronological_split_orders_without_leakage():
    samples = build_samples_from_candles("BTCUSDT", make_candles(60))
    train, test = chronological_split(samples, train_ratio=0.7)
    assert train
    assert test
    assert max(s.timestamp for s in train) <= min(s.timestamp for s in test)


def test_model_save_load(tmp_path):
    samples = build_samples_from_candles("BTCUSDT", make_candles(60, jump_index=35))
    model = train_model(samples)
    path = model.save(tmp_path / "model.json")
    loaded = CryptoSignalModel.load(path)
    assert loaded.samples == model.samples
    assert loaded.feature_names == model.feature_names


def test_scanner_result_includes_ml_score_when_model_supplied():
    candles = make_candles(60, jump_index=40)
    samples = build_samples_from_candles("BTCUSDT", candles)
    model = train_model(samples)
    result = evaluate_symbol("BTCUSDT", Market.CRYPTO, candles, model=model)
    assert result.ml_probability is not None
    assert result.ml_score is not None
    assert result.combined_opportunity_score is not None
    assert "ML" in result.explanation


def test_low_sample_warning():
    sample = MLSample("BTCUSDT", "2025-01-01T00:00:00", {name: 0.0 for name in ["ma_ratio"]}, 1)
    model = CryptoSignalModel(["ma_ratio"], {"ma_ratio": 0.0}, 0.0, {"ma_ratio": 0.0}, {"ma_ratio": 1.0}, 0.5, 1)
    metrics = evaluate_model([sample], model)
    assert "low" in metrics["warning"].lower()
