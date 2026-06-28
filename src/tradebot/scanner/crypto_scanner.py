from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tradebot.data.csv_loader import CSVValidationError, load_candles
from tradebot.ml.crypto_signal_model import CryptoSignalModel, extract_features
from tradebot.models import Action, Candle, Market, ScanResult, Signal
from tradebot.risk.cost_engine import CostEngine
from tradebot.risk.tax_engine import TaxEngine
from tradebot.strategies.momentum import MomentumVolumeStrategy
from tradebot.strategies.base import avg


@dataclass(frozen=True)
class ScannerConfig:
    short_window: int = 7
    long_window: int = 21
    range_window: int = 20
    min_candles: int = 30
    min_average_volume: float = 1000.0
    min_expected_net_percent: float = 0.20
    low_volatility_threshold: float = 0.01
    extreme_volatility_threshold: float = 0.35
    tax_buffer_percent: float = 0.30
    assumed_quantity: float = 1.0


def scan_crypto_folder(folder: str | Path, top: int | None = None, config: ScannerConfig | None = None, model: CryptoSignalModel | None = None) -> list[ScanResult]:
    return _scan(folder, Market.CRYPTO, top=top, config=config, model=model)


def _scan(
    folder: str | Path,
    market: Market,
    top: int | None = None,
    config: ScannerConfig | None = None,
    model: CryptoSignalModel | None = None,
) -> list[ScanResult]:
    config = config or ScannerConfig()
    results: list[ScanResult] = []
    for path in sorted(Path(folder).glob("*.csv")):
        try:
            candles = load_candles(path)
            result = evaluate_symbol(path.stem, market, candles, config=config, model=model)
        except (CSVValidationError, ValueError) as exc:
            signal = Signal(Action.HOLD, 0.0, "Rejected invalid CSV data", 0.0, 100.0)
            result = ScanResult(
                path.stem,
                market,
                signal,
                0.0,
                0.0,
                100.0,
                0.0,
                0.0,
                0.0,
                opportunity_score=0.0,
                risk_score=100.0,
                confidence=0.0,
                rejected=True,
                rejection_reason=str(exc),
                explanation="Rejected because candle data could not be loaded or validated.",
            )
        results.append(result)

    ranked = sorted(results, key=lambda item: (item.rejected, -(item.combined_opportunity_score if item.combined_opportunity_score is not None else item.opportunity_score), item.risk_score, item.symbol))
    ranked = [ _with_rank(result, index) for index, result in enumerate(ranked, start=1) ]
    return ranked[:top] if top else ranked


def evaluate_symbol(symbol: str, market: Market, candles: list[Candle], config: ScannerConfig | None = None, model: CryptoSignalModel | None = None) -> ScanResult:
    config = config or ScannerConfig()
    strategy = MomentumVolumeStrategy()
    signal = strategy.generate_signal(candles)
    if len(candles) < config.min_candles:
        return _rejected(symbol, market, signal, "too_few_candles", "Rejected: too few candles for reliable scanner ranking.")

    recent = candles[-config.range_window:]
    last = candles[-1]
    closes = [c.close for c in candles]
    volumes = [c.volume for c in candles]
    short_ma = avg(closes[-config.short_window:])
    long_ma = avg(closes[-config.long_window:])
    recent_high = max(c.high for c in recent)
    recent_low = min(c.low for c in recent)
    average_volume = avg(volumes[-config.long_window:])
    previous_average_volume = avg(volumes[-config.long_window * 2 : -config.long_window]) or average_volume

    trend_strength = _clamp01((short_ma / long_ma - 1.0) / 0.08 + 0.5) if long_ma else 0.0
    close_position = _clamp01((last.close - recent_low) / max(recent_high - recent_low, 1e-9))
    trend_quality = _clamp01(trend_strength * 0.65 + close_position * 0.35)

    latest_volume_strength = last.volume / max(average_volume, 1.0)
    volume_growth = average_volume / max(previous_average_volume, 1.0)
    volume_strength = _clamp01((latest_volume_strength - 0.5) / 2.0) * 0.65 + _clamp01((volume_growth - 0.8) / 1.0) * 0.35

    volatility_percent = (recent_high - recent_low) / max(last.close, 1e-9)
    volatility_risk = _volatility_risk(volatility_percent, config)
    liquidity_safety = _clamp01(average_volume / max(config.min_average_volume * 5, 1.0))

    resistance_distance = (recent_high - last.close) / max(last.close, 1e-9)
    breakout_quality = _clamp01(1.0 - max(resistance_distance, 0.0) / 0.04)
    if last.close >= recent_high * 0.995:
        breakout_quality = _clamp01(breakout_quality + 0.20)
    breakout_quality = _clamp01(breakout_quality * _clamp01(latest_volume_strength / 1.5))

    support_distance = (last.close - short_ma) / max(last.close, 1e-9)
    recent_return = (last.close - candles[-5].close) / max(candles[-5].close, 1e-9)
    pullback_quality = _clamp01(1.0 - abs(support_distance) / 0.035)
    if recent_return < -0.10:
        pullback_quality *= 0.25
    if short_ma < long_ma:
        pullback_quality *= 0.5

    expected_move_percent = _expected_move(trend_quality, breakout_quality, pullback_quality, volatility_percent)
    net_percent = _net_profit_feasibility(market, last.close, expected_move_percent, config)
    net_profit_score = _clamp01(net_percent / max(config.min_expected_net_percent * 3.0, 1e-9))

    rejection_reason = _rejection_reason(candles, average_volume, volatility_percent, net_percent, config)
    rejected = bool(rejection_reason)
    risk_score = min(100.0, max(0.0, volatility_risk * 55.0 + (1.0 - liquidity_safety) * 30.0 + (1.0 - net_profit_score) * 15.0))
    base_opportunity_score = 0.0 if rejected else min(
        100.0,
        max(
            0.0,
            trend_quality * 25.0
            + volume_strength * 20.0
            + breakout_quality * 18.0
            + pullback_quality * 12.0
            + liquidity_safety * 15.0
            + net_profit_score * 10.0
            - risk_score * 0.25,
        ),
    )
    ml_probability, ml_score, ml_explanation = _ml_score(model, candles)
    opportunity_score = base_opportunity_score if ml_score is None or rejected else min(100.0, max(0.0, base_opportunity_score * 0.75 + ml_score * 0.25))
    action = Action.HOLD if rejected else (Action.BUY if opportunity_score >= 55.0 else signal.action)
    confidence = 0.0 if rejected else min(1.0, opportunity_score / 100.0 * (1.0 - risk_score / 140.0))
    explanation = _explanation(trend_quality, volume_strength, breakout_quality, pullback_quality, net_percent, rejected, rejection_reason)
    if ml_explanation:
        explanation = f"{explanation} {ml_explanation}"

    return ScanResult(
        symbol=symbol,
        market=market,
        signal=Signal(action, opportunity_score / 100.0, explanation, confidence, risk_score / 100.0),
        volume_strength=volume_strength,
        trend_strength=trend_quality,
        volatility_risk=volatility_risk,
        liquidity_safety=liquidity_safety,
        net_profit_possibility=net_percent / 100.0,
        rank_score=opportunity_score,
        opportunity_score=base_opportunity_score,
        risk_score=risk_score,
        confidence=confidence,
        expected_move_percent=expected_move_percent,
        estimated_net_profit_after_cost_tax=net_percent,
        rejected=rejected,
        rejection_reason=rejection_reason,
        explanation=explanation,
        breakout_quality=breakout_quality,
        pullback_quality=pullback_quality,
        ml_probability=ml_probability,
        ml_score=ml_score,
        combined_opportunity_score=opportunity_score,
        ml_explanation=ml_explanation,
    )


def _with_rank(result: ScanResult, rank: int) -> ScanResult:
    return ScanResult(**{**result.__dict__, "rank": rank})


def _rejected(symbol: str, market: Market, signal: Signal, reason: str, explanation: str) -> ScanResult:
    return ScanResult(
        symbol=symbol,
        market=market,
        signal=Signal(Action.HOLD, 0.0, explanation, 0.0, 1.0),
        volume_strength=0.0,
        trend_strength=0.0,
        volatility_risk=1.0,
        liquidity_safety=0.0,
        net_profit_possibility=0.0,
        rank_score=0.0,
        opportunity_score=0.0,
        risk_score=100.0,
        confidence=0.0,
        rejected=True,
        rejection_reason=reason,
        explanation=explanation,
    )


def _ml_score(model: CryptoSignalModel | None, candles: list[Candle]) -> tuple[float | None, float | None, str]:
    if model is None:
        return None, None, ""
    try:
        features = extract_features(candles, len(candles) - 1)
    except ValueError:
        return None, None, "ML skipped: not enough historical candles."
    probability = model.predict_probability(features)
    score = probability * 100.0
    direction = "supported" if probability >= 0.55 else "weakened" if probability <= 0.45 else "neutral"
    return probability, score, f"ML {direction} signal with probability={probability:.2f}."


def _volatility_risk(volatility_percent: float, config: ScannerConfig) -> float:
    if volatility_percent < config.low_volatility_threshold:
        return 0.85
    if volatility_percent > config.extreme_volatility_threshold:
        return 1.0
    ideal = 0.08
    return _clamp01(abs(volatility_percent - ideal) / config.extreme_volatility_threshold)


def _expected_move(trend_quality: float, breakout_quality: float, pullback_quality: float, volatility_percent: float) -> float:
    setup_quality = max(breakout_quality, pullback_quality * 0.75)
    expected = (0.015 + trend_quality * 0.025 + setup_quality * 0.035 + min(volatility_percent, 0.12) * 0.20) * 100.0
    return min(18.0, max(0.0, expected))


def _net_profit_feasibility(market: Market, price: float, expected_move_percent: float, config: ScannerConfig) -> float:
    exit_price = price * (1.0 + expected_move_percent / 100.0)
    costs = CostEngine().estimate(market, price, exit_price, config.assumed_quantity)
    gross = exit_price - price
    tax_buffer = max(0.0, gross) * config.tax_buffer_percent
    tax_estimate = TaxEngine().estimate(market, gross)["tax"]
    net = gross - costs["total_cost"] - max(tax_buffer, tax_estimate)
    return net / max(price, 1e-9) * 100.0


def _rejection_reason(candles: list[Candle], average_volume: float, volatility_percent: float, net_percent: float, config: ScannerConfig) -> str:
    if len(candles) < config.min_candles:
        return "too_few_candles"
    if average_volume < config.min_average_volume:
        return "low_liquidity"
    if volatility_percent > config.extreme_volatility_threshold:
        return "extreme_volatility"
    if volatility_percent < config.low_volatility_threshold:
        return "flat_or_dead_market"
    if net_percent < config.min_expected_net_percent:
        return "weak_after_cost_tax_profit"
    return ""


def _explanation(
    trend_quality: float,
    volume_strength: float,
    breakout_quality: float,
    pullback_quality: float,
    net_percent: float,
    rejected: bool,
    rejection_reason: str,
) -> str:
    if rejected:
        return f"Rejected by scanner: {rejection_reason}."
    dominant = "breakout" if breakout_quality >= pullback_quality else "pullback"
    return (
        f"{dominant.title()} setup with trend={trend_quality:.2f}, volume={volume_strength:.2f}, "
        f"breakout={breakout_quality:.2f}, pullback={pullback_quality:.2f}, estimated net={net_percent:.2f}%."
    )


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))
