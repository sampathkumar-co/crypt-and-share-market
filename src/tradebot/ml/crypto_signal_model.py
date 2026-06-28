from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tradebot.data.csv_loader import load_candles
from tradebot.models import Candle
from tradebot.strategies.base import avg

FEATURE_NAMES = [
    "ma_ratio",
    "return_1",
    "return_3",
    "return_7",
    "return_14",
    "volume_ratio",
    "volume_growth",
    "volatility",
    "close_position",
    "breakout_distance",
    "drawdown_from_high",
    "body_ratio",
    "upper_wick_ratio",
    "lower_wick_ratio",
]


@dataclass(frozen=True)
class LabelConfig:
    target_percent: float = 0.04
    stop_loss_percent: float = 0.02
    max_holding_bars: int = 10
    min_history: int = 21


@dataclass
class MLSample:
    symbol: str
    timestamp: str
    features: dict[str, float]
    label: int


@dataclass
class CryptoSignalModel:
    feature_names: list[str]
    weights: dict[str, float]
    bias: float
    means: dict[str, float]
    stds: dict[str, float]
    positive_rate: float
    samples: int
    label_config: dict[str, float | int] = field(default_factory=dict)
    model_type: str = "fallback_logistic_gradient"

    def predict_probability(self, features: dict[str, float]) -> float:
        z = self.bias
        for name in self.feature_names:
            std = self.stds.get(name, 1.0) or 1.0
            value = (features.get(name, 0.0) - self.means.get(name, 0.0)) / std
            z += self.weights.get(name, 0.0) * value
        return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))

    def save(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return output

    @classmethod
    def load(cls, path: str | Path) -> "CryptoSignalModel":
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))


def extract_features(candles: list[Candle], index: int) -> dict[str, float]:
    if index < 20:
        raise ValueError("At least 21 historical candles are required before feature extraction")
    history = candles[: index + 1]
    last = history[-1]
    closes = [c.close for c in history]
    volumes = [c.volume for c in history]
    recent = history[-20:]
    high = max(c.high for c in recent)
    low = min(c.low for c in recent)
    candle_range = max(last.high - last.low, 1e-9)
    short_ma = avg(closes[-7:])
    long_ma = avg(closes[-21:])
    avg_volume = avg(volumes[-14:])
    prev_volume = avg(volumes[-28:-14]) or avg_volume
    return {
        "ma_ratio": short_ma / long_ma - 1.0 if long_ma else 0.0,
        "return_1": _ret(closes, 1),
        "return_3": _ret(closes, 3),
        "return_7": _ret(closes, 7),
        "return_14": _ret(closes, 14),
        "volume_ratio": last.volume / max(avg_volume, 1.0),
        "volume_growth": avg_volume / max(prev_volume, 1.0),
        "volatility": (high - low) / max(last.close, 1e-9),
        "close_position": (last.close - low) / max(high - low, 1e-9),
        "breakout_distance": (last.close - high) / max(last.close, 1e-9),
        "drawdown_from_high": (high - last.close) / max(high, 1e-9),
        "body_ratio": abs(last.close - last.open) / candle_range,
        "upper_wick_ratio": (last.high - max(last.open, last.close)) / candle_range,
        "lower_wick_ratio": (min(last.open, last.close) - last.low) / candle_range,
    }


def generate_label(candles: list[Candle], index: int, config: LabelConfig | None = None) -> int:
    config = config or LabelConfig()
    entry = candles[index].close
    target = entry * (1.0 + config.target_percent)
    stop = entry * (1.0 - config.stop_loss_percent)
    future = candles[index + 1 : index + 1 + config.max_holding_bars]
    for candle in future:
        stop_hit = candle.low <= stop
        target_hit = candle.high >= target
        if target_hit and not stop_hit:
            return 1
        if stop_hit:
            return 0
        if target_hit:
            return 1
    return 0


def build_samples_from_candles(symbol: str, candles: list[Candle], config: LabelConfig | None = None) -> list[MLSample]:
    config = config or LabelConfig()
    samples: list[MLSample] = []
    last_index = len(candles) - config.max_holding_bars - 1
    for index in range(config.min_history, max(config.min_history, last_index + 1)):
        features = extract_features(candles, index)
        label = generate_label(candles, index, config)
        samples.append(MLSample(symbol, candles[index].timestamp.isoformat(), features, label))
    return samples


def load_samples_from_folder(folder: str | Path, config: LabelConfig | None = None) -> list[MLSample]:
    samples: list[MLSample] = []
    for path in sorted(Path(folder).glob("*.csv")):
        samples.extend(build_samples_from_candles(path.stem, load_candles(path), config))
    return sorted(samples, key=lambda sample: sample.timestamp)


def chronological_split(samples: list[MLSample], train_ratio: float = 0.7) -> tuple[list[MLSample], list[MLSample]]:
    ordered = sorted(samples, key=lambda sample: sample.timestamp)
    cut = int(len(ordered) * train_ratio)
    cut = min(max(cut, 1), max(len(ordered) - 1, 1)) if len(ordered) > 1 else len(ordered)
    return ordered[:cut], ordered[cut:]


def train_model(samples: list[MLSample], config: LabelConfig | None = None, epochs: int = 250, learning_rate: float = 0.08) -> CryptoSignalModel:
    if not samples:
        raise ValueError("No ML samples available for training")
    means, stds = _feature_stats(samples)
    weights = {name: 0.0 for name in FEATURE_NAMES}
    positive_rate = sum(sample.label for sample in samples) / len(samples)
    bias = math.log(max(positive_rate, 1e-4) / max(1.0 - positive_rate, 1e-4))
    for _ in range(epochs):
        gradients = {name: 0.0 for name in FEATURE_NAMES}
        bias_gradient = 0.0
        for sample in samples:
            z = bias + sum(weights[name] * _norm(sample.features.get(name, 0.0), means[name], stds[name]) for name in FEATURE_NAMES)
            pred = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
            err = pred - sample.label
            bias_gradient += err
            for name in FEATURE_NAMES:
                gradients[name] += err * _norm(sample.features.get(name, 0.0), means[name], stds[name])
        scale = 1.0 / len(samples)
        bias -= learning_rate * bias_gradient * scale
        for name in FEATURE_NAMES:
            weights[name] -= learning_rate * gradients[name] * scale
    return CryptoSignalModel(FEATURE_NAMES, weights, bias, means, stds, positive_rate, len(samples), asdict(config or LabelConfig()))


def train_from_folder(folder: str | Path, model_out: str | Path, config: LabelConfig | None = None) -> CryptoSignalModel:
    samples = load_samples_from_folder(folder, config)
    train, _test = chronological_split(samples)
    model = train_model(train, config)
    model.save(model_out)
    return model


def evaluate_model(samples: list[MLSample], model: CryptoSignalModel) -> dict:
    if not samples:
        return {"samples": 0, "accuracy": 0.0, "precision": 0.0, "recall": 0.0, "false_positive_rate": 0.0, "warning": "Sample size is too low for evaluation."}
    tp = fp = tn = fn = 0
    per_symbol: dict[str, dict[str, int]] = {}
    for sample in samples:
        pred = 1 if model.predict_probability(sample.features) >= 0.5 else 0
        if pred == 1 and sample.label == 1: tp += 1
        elif pred == 1 and sample.label == 0: fp += 1
        elif pred == 0 and sample.label == 0: tn += 1
        else: fn += 1
        row = per_symbol.setdefault(sample.symbol, {"samples": 0, "correct": 0})
        row["samples"] += 1
        row["correct"] += int(pred == sample.label)
    per_symbol_accuracy = {symbol: {"samples": row["samples"], "accuracy": row["correct"] / row["samples"]} for symbol, row in per_symbol.items()}
    return {
        "samples": len(samples),
        "accuracy": (tp + tn) / len(samples),
        "precision": tp / max(tp + fp, 1),
        "recall": tp / max(tp + fn, 1),
        "false_positive_rate": fp / max(fp + tn, 1),
        "per_symbol": per_symbol_accuracy,
        "warning": "Sample size is too low for reliable ML conclusions." if len(samples) < 100 else "",
    }


def evaluate_folder(folder: str | Path, model_path: str | Path) -> dict:
    samples = load_samples_from_folder(folder)
    train, test = chronological_split(samples)
    model = CryptoSignalModel.load(model_path)
    metrics = evaluate_model(test, model)
    metrics["train_test_split"] = {"train_samples": len(train), "test_samples": len(test), "split": "chronological_70_30"}
    return metrics


def _ret(closes: list[float], bars: int) -> float:
    return closes[-1] / closes[-1 - bars] - 1.0 if len(closes) > bars and closes[-1 - bars] else 0.0


def _feature_stats(samples: list[MLSample]) -> tuple[dict[str, float], dict[str, float]]:
    means = {name: avg([sample.features.get(name, 0.0) for sample in samples]) for name in FEATURE_NAMES}
    stds: dict[str, float] = {}
    for name in FEATURE_NAMES:
        variance = avg([(sample.features.get(name, 0.0) - means[name]) ** 2 for sample in samples])
        stds[name] = math.sqrt(variance) or 1.0
    return means, stds


def _norm(value: float, mean: float, std: float) -> float:
    return (value - mean) / (std or 1.0)
