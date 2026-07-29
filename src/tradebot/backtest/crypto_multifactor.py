from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any

from tradebot.backtest.metrics import max_drawdown
from tradebot.backtest.research_gate import dataset_fingerprint, load_histories
from tradebot.models import Candle, Market
from tradebot.risk.cost_engine import CostEngine
from tradebot.risk.tax_engine import TaxEngine

SCHEMA_VERSION = "1.0"
REQUIRED_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "ADAUSDT")

FULL_FACTOR_WEIGHTS = {
    "momentum_14": 0.07,
    "momentum_60": 0.14,
    "momentum_180_ex_7": 0.12,
    "trend_quality": 0.13,
    "ma_distance": 0.07,
    "breakout_position": 0.06,
    "volume_confirmation": 0.06,
    "up_volume_ratio": 0.04,
    "log_liquidity": 0.03,
    "low_volatility": 0.06,
    "low_downside_volatility": 0.07,
    "drawdown_quality": 0.05,
    "low_overextension": 0.05,
    "btc_beta_quality": 0.05,
}
PRICE_FACTOR_WEIGHTS = {
    "momentum_14": 0.12,
    "momentum_60": 0.24,
    "momentum_180_ex_7": 0.20,
    "trend_quality": 0.20,
    "ma_distance": 0.10,
    "breakout_position": 0.14,
}


@dataclass(frozen=True)
class MultiFactorVariant:
    name: str
    factor_set: str
    rebalance_bars: int
    top_n: int
    min_cash_reserve: float
    max_asset_weight: float
    target_volatility: float
    min_score: float
    max_pair_correlation: float = 0.93


VARIANTS = (
    MultiFactorVariant("primary_full", "full", 14, 3, 0.25, 0.35, 0.28, 0.58),
    MultiFactorVariant("conservative_full", "full", 21, 2, 0.40, 0.30, 0.20, 0.62),
    MultiFactorVariant("diversified_full", "full", 14, 3, 0.30, 0.30, 0.24, 0.56, 0.96),
    MultiFactorVariant("price_only", "price", 14, 3, 0.25, 0.35, 0.28, 0.58),
    MultiFactorVariant("simple_trend", "simple", 14, 2, 0.20, 0.40, 0.30, 0.0),
)


@dataclass(frozen=True)
class CryptoMultiFactorConfig:
    history_bars: int = 1800
    warmup_bars: int = 240
    early_periods: int = 6
    late_periods: int = 6
    test_bars: int = 120
    middle_embargo_bars: int = 30
    final_embargo_bars: int = 90
    min_trade_weight: float = 0.04
    extra_cost_per_turnover: float = 0.0015
    starting_cash: float = 100000.0
    min_active_periods: int = 8
    min_positive_periods: int = 7
    max_portfolio_drawdown: float = 0.15
    min_positive_multifactor_variants: int = 2
    min_leave_one_out_positive_fraction: float = 0.80

    def __post_init__(self) -> None:
        used = (
            self.warmup_bars
            + (self.early_periods + self.late_periods) * self.test_bars
            + self.middle_embargo_bars
            + self.final_embargo_bars
        )
        if used != self.history_bars:
            raise ValueError("frozen warmup, tests and embargoes must consume exactly 1800 bars")
        if self.warmup_bars < 200:
            raise ValueError("warmup is too short for the 200-day market regime")
        if self.starting_cash <= 0:
            raise ValueError("starting_cash must be positive")
        if not 0 <= self.min_trade_weight < 1:
            raise ValueError("min_trade_weight must be between zero and one")

    @property
    def total_periods(self) -> int:
        return self.early_periods + self.late_periods


@dataclass
class MultiFactorPeriod:
    variant: str
    period: int
    phase: str
    test_start: str
    test_end: str
    net_return: float
    stressed_return: float
    equal_weight_buy_hold_return: float
    btc_buy_hold_return: float
    excess_vs_equal_weight: float
    max_drawdown: float
    turnover: float
    transactions: int
    active: bool
    average_cash_weight: float
    selected_symbols: list[str]
    regime_counts: dict[str, int]
    total_fees: float
    total_slippage: float
    total_tax: float


@dataclass
class MultiFactorSummary:
    variant: str
    periods: list[MultiFactorPeriod]
    active_periods: int
    positive_periods: int
    average_return: float
    median_return: float
    compounded_return: float
    positive_fraction: float
    early_average_return: float
    late_average_return: float
    average_stressed_return: float
    average_equal_weight_return: float
    average_excess_vs_equal_weight: float
    beat_equal_weight_fraction: float
    worst_drawdown: float
    average_turnover: float


@dataclass
class CryptoMultiFactorReport:
    schema_version: str
    generated_at: str
    market: str
    symbols: list[str]
    dataset_fingerprint: str
    dataset_start: str
    dataset_end: str
    config: dict[str, Any]
    factor_weights: dict[str, dict[str, float]]
    variants: list[MultiFactorSummary]
    primary_beats_price_only_fraction: float
    primary_beats_simple_trend_fraction: float
    leave_one_out_average_returns: dict[str, float]
    leave_one_out_positive_fraction: float
    accepted: bool
    eligible_for_forward_paper: bool
    reasons: list[str]
    paper_only: bool = True
    authorizes_real_trading: bool = False


@dataclass
class _Position:
    quantity: float
    average_price: float
    entry_time: datetime


@dataclass(frozen=True)
class _Regime:
    name: str
    base_exposure: float
    breadth: float
    median_momentum: float
    median_volatility: float
    average_correlation: float


def _intersection_histories(folder: str | Path, config: CryptoMultiFactorConfig) -> dict[str, list[Candle]]:
    loaded = load_histories(folder)
    missing = sorted(set(REQUIRED_SYMBOLS) - set(loaded))
    if missing:
        raise ValueError(f"Missing required histories: {', '.join(missing)}")
    common = sorted(set.intersection(*(set(c.timestamp for c in loaded[s]) for s in REQUIRED_SYMBOLS)))
    if len(common) < config.history_bars:
        raise ValueError(f"Only {len(common)} aligned candles are available; {config.history_bars} are required")
    chosen = common[-config.history_bars :]
    chosen_set = set(chosen)
    aligned: dict[str, list[Candle]] = {}
    for symbol in REQUIRED_SYMBOLS:
        mapping = {c.timestamp: c for c in loaded[symbol] if c.timestamp in chosen_set}
        aligned[symbol] = [mapping[t] for t in chosen]
        if len(aligned[symbol]) != config.history_bars:
            raise ValueError(f"{symbol} is incomplete after timestamp alignment")
    return aligned


def _simple_return(candles: list[Candle], lookback: int, skip: int = 0) -> float:
    end_index = len(candles) - 1 - skip
    start_index = end_index - lookback
    if start_index < 0 or end_index < 0:
        return 0.0
    base = candles[start_index].close
    return candles[end_index].close / base - 1.0 if base > 0 else 0.0


def _return_series(candles: list[Candle], window: int) -> list[float]:
    closes = [c.close for c in candles[-(window + 1) :]]
    return [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1] > 0]


def _annualized_volatility(candles: list[Candle], window: int, downside: bool = False) -> float:
    values = _return_series(candles, window)
    if downside:
        values = [min(value, 0.0) for value in values]
    if len(values) < 2:
        return 0.0
    return pstdev(values) * math.sqrt(365.0)


def _close_drawdown(candles: list[Candle], window: int) -> float:
    closes = [c.close for c in candles[-window:]]
    if not closes:
        return 0.0
    peak = closes[0]
    worst = 0.0
    for value in closes:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def _trend_quality(candles: list[Candle], window: int = 120) -> float:
    values = [math.log(max(c.close, 1e-12)) for c in candles[-window:]]
    n = len(values)
    if n < 3:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = mean(values)
    xx = sum((i - x_mean) ** 2 for i in range(n))
    yy = sum((value - y_mean) ** 2 for value in values)
    if xx <= 0 or yy <= 0:
        return 0.0
    xy = sum((i - x_mean) * (value - y_mean) for i, value in enumerate(values))
    slope = xy / xx
    r_squared = max(0.0, min(1.0, (xy * xy) / (xx * yy)))
    annualized_slope = math.exp(max(-0.02, min(0.02, slope)) * 365.0) - 1.0
    return max(annualized_slope, 0.0) * r_squared


def _correlation(left: list[float], right: list[float]) -> float:
    n = min(len(left), len(right))
    if n < 3:
        return 0.0
    x = left[-n:]
    y = right[-n:]
    x_mean = mean(x)
    y_mean = mean(y)
    numerator = sum((a - x_mean) * (b - y_mean) for a, b in zip(x, y))
    x_var = sum((a - x_mean) ** 2 for a in x)
    y_var = sum((b - y_mean) ** 2 for b in y)
    if x_var <= 0 or y_var <= 0:
        return 0.0
    return max(-1.0, min(1.0, numerator / math.sqrt(x_var * y_var)))


def _beta(asset_returns: list[float], btc_returns: list[float]) -> float:
    n = min(len(asset_returns), len(btc_returns))
    if n < 3:
        return 1.0
    asset = asset_returns[-n:]
    btc = btc_returns[-n:]
    asset_mean = mean(asset)
    btc_mean = mean(btc)
    covariance = sum((a - asset_mean) * (b - btc_mean) for a, b in zip(asset, btc))
    variance = sum((b - btc_mean) ** 2 for b in btc)
    return covariance / variance if variance > 0 else 1.0


def _feature_row(candles: list[Candle], btc_candles: list[Candle]) -> dict[str, float]:
    close = candles[-1].close
    ma100 = mean(c.close for c in candles[-100:])
    highs = [c.high for c in candles[-90:]]
    lows = [c.low for c in candles[-90:]]
    range_low = min(lows)
    range_high = max(highs)
    breakout = (close - range_low) / (range_high - range_low) if range_high > range_low else 0.5
    volume20 = median(c.volume for c in candles[-20:])
    volume90 = median(c.volume for c in candles[-90:])
    volume_confirmation = volume20 / volume90 - 1.0 if volume90 > 0 else 0.0
    recent = candles[-61:]
    up_volume = 0.0
    down_volume = 0.0
    for previous, current in zip(recent, recent[1:]):
        if current.close >= previous.close:
            up_volume += current.volume
        else:
            down_volume += current.volume
    up_volume_ratio = math.log((up_volume + 1.0) / (down_volume + 1.0))
    liquidity = median(c.close * c.volume for c in candles[-30:])
    vol30 = _annualized_volatility(candles, 30)
    downside60 = _annualized_volatility(candles, 60, downside=True)
    drawdown90 = _close_drawdown(candles, 90)
    momentum7 = _simple_return(candles, 7)
    daily_vol = vol30 / math.sqrt(365.0) if vol30 > 0 else 0.0
    overextension = max(0.0, momentum7 - 2.0 * daily_vol * math.sqrt(7.0))
    asset_returns = _return_series(candles, 60)
    btc_returns = _return_series(btc_candles, 60)
    beta = _beta(asset_returns, btc_returns)
    beta_penalty = abs(beta - 1.0) + max(0.0, beta - 1.5)
    return {
        "momentum_14": _simple_return(candles, 14),
        "momentum_60": _simple_return(candles, 60),
        "momentum_180_ex_7": _simple_return(candles, 180, skip=7),
        "trend_quality": _trend_quality(candles, 120),
        "ma_distance": close / ma100 - 1.0 if ma100 > 0 else 0.0,
        "breakout_position": breakout,
        "volume_confirmation": volume_confirmation,
        "up_volume_ratio": up_volume_ratio,
        "log_liquidity": math.log(max(liquidity, 1.0)),
        "volatility_30": vol30,
        "downside_volatility_60": downside60,
        "drawdown_90": drawdown90,
        "overextension_7": overextension,
        "btc_beta": beta,
        "btc_beta_penalty": beta_penalty,
        "btc_correlation": _correlation(asset_returns, btc_returns),
    }


def _percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    if len(values) <= 1:
        return {key: 0.5 for key in values}
    output: dict[str, float] = {}
    denominator = len(values) - 1
    for key, value in values.items():
        lower = sum(other < value for other in values.values())
        equal = sum(other == value for other in values.values())
        output[key] = (lower + 0.5 * (equal - 1)) / denominator
    return output


def _scores(features: dict[str, dict[str, float]], factor_set: str) -> dict[str, float]:
    weights = FULL_FACTOR_WEIGHTS if factor_set == "full" else PRICE_FACTOR_WEIGHTS
    transformed: dict[str, dict[str, float]] = {}
    for factor in weights:
        if factor == "low_volatility":
            transformed[factor] = {s: -row["volatility_30"] for s, row in features.items()}
        elif factor == "low_downside_volatility":
            transformed[factor] = {s: -row["downside_volatility_60"] for s, row in features.items()}
        elif factor == "drawdown_quality":
            transformed[factor] = {s: row["drawdown_90"] for s, row in features.items()}
        elif factor == "low_overextension":
            transformed[factor] = {s: -row["overextension_7"] for s, row in features.items()}
        elif factor == "btc_beta_quality":
            transformed[factor] = {s: -row["btc_beta_penalty"] for s, row in features.items()}
        else:
            transformed[factor] = {s: row[factor] for s, row in features.items()}
    ranks = {factor: _percentile_ranks(values) for factor, values in transformed.items()}
    return {
        symbol: sum(weights[factor] * ranks[factor][symbol] for factor in weights)
        for symbol in features
    }


def _average_pairwise_correlation(prior: dict[str, list[Candle]]) -> float:
    symbols = sorted(prior)
    values: list[float] = []
    series = {symbol: _return_series(prior[symbol], 60) for symbol in symbols}
    for index, left in enumerate(symbols):
        for right in symbols[index + 1 :]:
            values.append(_correlation(series[left], series[right]))
    return mean(values) if values else 0.0


def _market_regime(features: dict[str, dict[str, float]], prior: dict[str, list[Candle]]) -> _Regime:
    proxy_symbol = "BTCUSDT" if "BTCUSDT" in features else max(features, key=lambda s: features[s]["log_liquidity"])
    proxy = features[proxy_symbol]
    breadth = sum(row["momentum_60"] > 0 and row["ma_distance"] > 0 for row in features.values()) / len(features)
    median_momentum = median(row["momentum_60"] for row in features.values())
    median_volatility = median(row["volatility_30"] for row in features.values())
    average_correlation = _average_pairwise_correlation(prior)
    crisis = (
        proxy["drawdown_90"] <= -0.25
        or median_volatility >= 1.10
        or (average_correlation >= 0.92 and median_momentum <= -0.05)
    )
    bear = (proxy["ma_distance"] < 0 and proxy["momentum_60"] < 0) or breadth < 0.25
    bull = (
        proxy["ma_distance"] > 0
        and proxy["momentum_60"] > 0
        and proxy["momentum_180_ex_7"] > 0
        and breadth >= 0.60
        and median_momentum > 0
    )
    if crisis:
        return _Regime("crisis", 0.0, breadth, median_momentum, median_volatility, average_correlation)
    if bear:
        return _Regime("bear", 0.0, breadth, median_momentum, median_volatility, average_correlation)
    if bull:
        exposure = 0.75
        if average_correlation > 0.85:
            exposure *= 0.80
        return _Regime("bull", exposure, breadth, median_momentum, median_volatility, average_correlation)
    exposure = 0.35 if breadth >= 0.40 else 0.20
    return _Regime("neutral", exposure, breadth, median_momentum, median_volatility, average_correlation)


def _pair_correlation(left: str, right: str, prior: dict[str, list[Candle]]) -> float:
    return _correlation(_return_series(prior[left], 60), _return_series(prior[right], 60))


def _portfolio_volatility(weights: dict[str, float], prior: dict[str, list[Candle]]) -> float:
    if not weights:
        return 0.0
    series = {symbol: _return_series(prior[symbol], 60) for symbol in weights}
    n = min((len(values) for values in series.values()), default=0)
    if n < 3:
        return 0.0
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return 0.0
    combined = [
        sum((weight / total_weight) * series[symbol][index] for symbol, weight in weights.items())
        for index in range(-n, 0)
    ]
    return pstdev(combined) * math.sqrt(365.0)


def _capped_weights(raw: dict[str, float], exposure: float, cap: float) -> dict[str, float]:
    if not raw or exposure <= 0:
        return {}
    pending = {key: max(value, 0.0) for key, value in raw.items() if value > 0}
    remaining = exposure
    output: dict[str, float] = {}
    while pending and remaining > 1e-12:
        denominator = sum(pending.values())
        if denominator <= 0:
            break
        capped = []
        for symbol, value in pending.items():
            proposed = remaining * value / denominator
            if proposed >= cap - 1e-12:
                output[symbol] = cap
                capped.append(symbol)
        if not capped:
            for symbol, value in pending.items():
                output[symbol] = remaining * value / denominator
            break
        for symbol in capped:
            remaining -= output[symbol]
            pending.pop(symbol)
    return output


def _simple_trend_target(prior: dict[str, list[Candle]], variant: MultiFactorVariant) -> tuple[dict[str, float], str]:
    rows: dict[str, dict[str, float]] = {}
    for symbol, candles in prior.items():
        close = candles[-1].close
        ma120 = mean(c.close for c in candles[-120:])
        fast = _simple_return(candles, 30)
        slow = _simple_return(candles, 90)
        vol = _annualized_volatility(candles, 30)
        rows[symbol] = {
            "fast": fast,
            "slow": slow,
            "vol": vol,
            "trend": 1.0 if close > ma120 and fast > 0 and slow > 0 else 0.0,
            "score": (0.4 * fast + 0.6 * slow) / max(vol, 0.10),
        }
    breadth = sum(row["trend"] > 0 for row in rows.values()) / len(rows)
    proxy = rows.get("BTCUSDT") or rows[max(rows, key=lambda s: prior[s][-1].close * prior[s][-1].volume)]
    if breadth < 0.60 or median(row["slow"] for row in rows.values()) <= 0 or proxy["trend"] <= 0:
        return {}, "cash"
    eligible = sorted(
        ((symbol, row) for symbol, row in rows.items() if row["trend"] > 0 and row["score"] > 0),
        key=lambda item: (item[1]["score"], item[0]),
        reverse=True,
    )[: variant.top_n]
    raw = {symbol: 1.0 / max(row["vol"], 0.05) for symbol, row in eligible}
    return _capped_weights(raw, 1.0 - variant.min_cash_reserve, variant.max_asset_weight), "trend"


def _target_weights(
    prior: dict[str, list[Candle]],
    variant: MultiFactorVariant,
    drawdown_multiplier: float,
) -> tuple[dict[str, float], str]:
    if variant.factor_set == "simple":
        return _simple_trend_target(prior, variant)
    btc = prior.get("BTCUSDT") or prior[next(iter(prior))]
    features = {symbol: _feature_row(candles, btc) for symbol, candles in prior.items()}
    regime = _market_regime(features, prior)
    if regime.base_exposure <= 0 or drawdown_multiplier <= 0:
        return {}, regime.name
    scores = _scores(features, variant.factor_set)
    eligible = []
    for symbol, row in features.items():
        score = scores[symbol]
        absolute_ok = (
            row["drawdown_90"] > -0.45
            and row["volatility_30"] < 1.50
            and row["downside_volatility_60"] < 1.60
            and row["log_liquidity"] > 0
            and row["trend_quality"] > 0
        )
        if regime.name == "bull":
            absolute_ok = absolute_ok and row["momentum_60"] > 0 and row["momentum_180_ex_7"] > 0 and row["ma_distance"] > 0
        else:
            absolute_ok = absolute_ok and row["momentum_14"] > 0 and row["momentum_60"] > -0.02 and row["ma_distance"] > -0.01
        if absolute_ok and score >= variant.min_score:
            eligible.append((symbol, score, row))
    eligible.sort(key=lambda item: (item[1], item[0]), reverse=True)
    selected: list[tuple[str, float, dict[str, float]]] = []
    for candidate in eligible:
        symbol = candidate[0]
        if all(_pair_correlation(symbol, chosen[0], prior) <= variant.max_pair_correlation for chosen in selected):
            selected.append(candidate)
        if len(selected) >= variant.top_n:
            break
    if not selected:
        return {}, regime.name
    raw = {
        symbol: (0.5 + score) / max(row["downside_volatility_60"], 0.08)
        for symbol, score, row in selected
    }
    unit = {symbol: value / sum(raw.values()) for symbol, value in raw.items()}
    estimated_vol = _portfolio_volatility(unit, prior)
    max_exposure = 1.0 - variant.min_cash_reserve
    exposure = min(max_exposure, regime.base_exposure * drawdown_multiplier)
    if estimated_vol > 0:
        exposure = min(exposure, variant.target_volatility / estimated_vol)
    average_score = mean(score for _, score, _ in selected)
    confidence = max(0.45, min(1.0, 0.50 + 0.50 * (average_score - variant.min_score) / max(1.0 - variant.min_score, 1e-9)))
    exposure *= confidence
    return _capped_weights(raw, exposure, variant.max_asset_weight), regime.name


def _period_bounds(period: int, config: CryptoMultiFactorConfig) -> tuple[int, int, str]:
    if not 1 <= period <= config.total_periods:
        raise ValueError("period outside frozen range")
    if period <= config.early_periods:
        start = config.warmup_bars + (period - 1) * config.test_bars
        phase = "early"
    else:
        start = (
            config.warmup_bars
            + config.early_periods * config.test_bars
            + config.middle_embargo_bars
            + (period - config.early_periods - 1) * config.test_bars
        )
        phase = "late"
    return start, start + config.test_bars - 1, phase


def _benchmark_return(histories: dict[str, list[Candle]], start: int, end: int) -> tuple[float, float]:
    returns = []
    for candles in histories.values():
        first = candles[start].open
        last = candles[end].close
        returns.append(last / first - 1.0 if first > 0 else 0.0)
    btc = histories.get("BTCUSDT") or histories[next(iter(histories))]
    first = btc[start].open
    btc_return = btc[end].close / first - 1.0 if first > 0 else 0.0
    return mean(returns) if returns else 0.0, btc_return


def _simulate_period(
    histories: dict[str, list[Candle]],
    period: int,
    variant: MultiFactorVariant,
    config: CryptoMultiFactorConfig,
) -> MultiFactorPeriod:
    start, end, phase = _period_bounds(period, config)
    cash = config.starting_cash
    positions: dict[str, _Position] = {}
    costs = CostEngine()
    taxes = TaxEngine()
    side_rate = costs.config.crypto_exchange_fee_pct + costs.config.crypto_slippage_pct
    total_fees = 0.0
    total_slippage = 0.0
    total_tax = 0.0
    total_turnover = 0.0
    transactions = 0
    selected_symbols: set[str] = set()
    cash_weights: list[float] = []
    regime_counts: dict[str, int] = {}
    curve = [cash]
    peak_equity = cash

    def mark(index: int, field: str = "close") -> float:
        return cash + sum(position.quantity * getattr(histories[symbol][index], field) for symbol, position in positions.items())

    def sell(symbol: str, quantity: float, price: float, timestamp: datetime) -> None:
        nonlocal cash, total_fees, total_slippage, total_tax, total_turnover, transactions
        position = positions[symbol]
        quantity = min(quantity, position.quantity)
        if quantity <= 1e-12:
            return
        notional = quantity * price
        fee = notional * costs.config.crypto_exchange_fee_pct
        slippage = notional * costs.config.crypto_slippage_pct
        gross = (price - position.average_price) * quantity
        holding_days = max(0, (timestamp.date() - position.entry_time.date()).days)
        tax = float(taxes.estimate(Market.CRYPTO, gross, holding_days, exit_value=notional)["tax"])
        cash += notional - fee - slippage - tax
        total_fees += fee
        total_slippage += slippage
        total_tax += tax
        total_turnover += notional
        transactions += 1
        remaining = position.quantity - quantity
        if remaining <= 1e-10:
            positions.pop(symbol)
        else:
            position.quantity = remaining

    def buy(symbol: str, notional: float, price: float, timestamp: datetime) -> None:
        nonlocal cash, total_fees, total_slippage, total_turnover, transactions
        if notional <= 0 or price <= 0:
            return
        affordable = cash / (1.0 + side_rate)
        notional = min(notional, affordable)
        if notional <= 1e-8:
            return
        fee = notional * costs.config.crypto_exchange_fee_pct
        slippage = notional * costs.config.crypto_slippage_pct
        quantity = notional / price
        cash -= notional + fee + slippage
        total_fees += fee
        total_slippage += slippage
        total_turnover += notional
        transactions += 1
        current = positions.get(symbol)
        if current is None:
            positions[symbol] = _Position(quantity, price, timestamp)
        else:
            combined = current.quantity + quantity
            current.average_price = (current.average_price * current.quantity + price * quantity) / combined
            current.quantity = combined

    for offset, index in enumerate(range(start, end + 1)):
        timestamp = histories[next(iter(histories))][index].timestamp
        if offset % variant.rebalance_bars == 0:
            equity_open = mark(index, "open")
            current_drawdown = max(0.0, 1.0 - equity_open / peak_equity) if peak_equity > 0 else 0.0
            if current_drawdown >= 0.15:
                drawdown_multiplier = 0.0
            elif current_drawdown >= 0.10:
                drawdown_multiplier = 0.25
            elif current_drawdown >= 0.05:
                drawdown_multiplier = 0.65
            else:
                drawdown_multiplier = 1.0
            prior = {symbol: candles[:index] for symbol, candles in histories.items()}
            target_weights, regime = _target_weights(prior, variant, drawdown_multiplier)
            regime_counts[regime] = regime_counts.get(regime, 0) + 1
            selected_symbols.update(target_weights)
            current_values = {symbol: position.quantity * histories[symbol][index].open for symbol, position in positions.items()}
            all_symbols = set(current_values) | set(target_weights)
            for symbol in sorted(all_symbols):
                target = equity_open * target_weights.get(symbol, 0.0)
                current_value = current_values.get(symbol, 0.0)
                difference = current_value - target
                if difference > config.min_trade_weight * equity_open and symbol in positions:
                    sell(symbol, difference / histories[symbol][index].open, histories[symbol][index].open, timestamp)
            equity_after_sales = mark(index, "open")
            for symbol in sorted(target_weights, key=lambda item: target_weights[item], reverse=True):
                target = equity_after_sales * target_weights[symbol]
                current_value = positions[symbol].quantity * histories[symbol][index].open if symbol in positions else 0.0
                difference = target - current_value
                if difference > config.min_trade_weight * equity_after_sales:
                    buy(symbol, difference, histories[symbol][index].open, timestamp)
        equity_close = mark(index, "close")
        peak_equity = max(peak_equity, equity_close)
        curve.append(equity_close)
        cash_weights.append(cash / equity_close if equity_close > 0 else 1.0)

    final_time = histories[next(iter(histories))][end].timestamp
    for symbol in list(positions):
        sell(symbol, positions[symbol].quantity, histories[symbol][end].close, final_time)
    curve.append(cash)
    equal_weight, btc_buy_hold = _benchmark_return(histories, start, end)
    net_return = cash / config.starting_cash - 1.0
    turnover = total_turnover / config.starting_cash
    return MultiFactorPeriod(
        variant=variant.name,
        period=period,
        phase=phase,
        test_start=histories[next(iter(histories))][start].timestamp.isoformat(),
        test_end=histories[next(iter(histories))][end].timestamp.isoformat(),
        net_return=net_return,
        stressed_return=net_return - turnover * config.extra_cost_per_turnover,
        equal_weight_buy_hold_return=equal_weight,
        btc_buy_hold_return=btc_buy_hold,
        excess_vs_equal_weight=net_return - equal_weight,
        max_drawdown=max_drawdown(curve),
        turnover=turnover,
        transactions=transactions,
        active=transactions > 0,
        average_cash_weight=mean(cash_weights) if cash_weights else 1.0,
        selected_symbols=sorted(selected_symbols),
        regime_counts=regime_counts,
        total_fees=total_fees,
        total_slippage=total_slippage,
        total_tax=total_tax,
    )


def _summarize(variant: MultiFactorVariant, periods: list[MultiFactorPeriod]) -> MultiFactorSummary:
    returns = [period.net_return for period in periods]
    early = [period.net_return for period in periods if period.phase == "early"]
    late = [period.net_return for period in periods if period.phase == "late"]
    return MultiFactorSummary(
        variant=variant.name,
        periods=periods,
        active_periods=sum(period.active for period in periods),
        positive_periods=sum(period.net_return > 0 for period in periods),
        average_return=mean(returns) if returns else 0.0,
        median_return=median(returns) if returns else 0.0,
        compounded_return=math.prod(1.0 + value for value in returns) - 1.0 if returns else 0.0,
        positive_fraction=sum(value > 0 for value in returns) / len(returns) if returns else 0.0,
        early_average_return=mean(early) if early else 0.0,
        late_average_return=mean(late) if late else 0.0,
        average_stressed_return=mean(period.stressed_return for period in periods) if periods else 0.0,
        average_equal_weight_return=mean(period.equal_weight_buy_hold_return for period in periods) if periods else 0.0,
        average_excess_vs_equal_weight=mean(period.excess_vs_equal_weight for period in periods) if periods else 0.0,
        beat_equal_weight_fraction=sum(period.net_return > period.equal_weight_buy_hold_return for period in periods) / len(periods) if periods else 0.0,
        worst_drawdown=max((period.max_drawdown for period in periods), default=0.0),
        average_turnover=mean(period.turnover for period in periods) if periods else 0.0,
    )


def _variant_result(
    histories: dict[str, list[Candle]],
    variant: MultiFactorVariant,
    config: CryptoMultiFactorConfig,
) -> MultiFactorSummary:
    periods = [_simulate_period(histories, period, variant, config) for period in range(1, config.total_periods + 1)]
    return _summarize(variant, periods)


def evaluate_crypto_multifactor(
    folder: str | Path,
    market: Market = Market.CRYPTO,
    config: CryptoMultiFactorConfig | None = None,
) -> CryptoMultiFactorReport:
    if market != Market.CRYPTO:
        raise ValueError("v1.0 multifactor validation is frozen to crypto")
    config = config or CryptoMultiFactorConfig()
    histories = _intersection_histories(folder, config)
    summaries = [_variant_result(histories, variant, config) for variant in VARIANTS]
    by_name = {summary.variant: summary for summary in summaries}
    primary = by_name["primary_full"]
    price_only = by_name["price_only"]
    simple = by_name["simple_trend"]
    primary_beats_price = sum(
        left.net_return > right.net_return for left, right in zip(primary.periods, price_only.periods)
    ) / len(primary.periods)
    primary_beats_simple = sum(
        left.net_return > right.net_return for left, right in zip(primary.periods, simple.periods)
    ) / len(primary.periods)
    leave_one_out: dict[str, float] = {}
    primary_variant = VARIANTS[0]
    for omitted in REQUIRED_SYMBOLS:
        subset = {symbol: candles for symbol, candles in histories.items() if symbol != omitted}
        leave_one_out[omitted] = _variant_result(subset, primary_variant, config).average_return
    leave_positive = sum(value > 0 for value in leave_one_out.values()) / len(leave_one_out)
    positive_multifactor = sum(
        by_name[name].average_return > 0
        and by_name[name].median_return > 0
        and by_name[name].compounded_return > 0
        for name in ("primary_full", "conservative_full", "diversified_full")
    )
    reasons: list[str] = []
    if len(primary.periods) != config.total_periods:
        reasons.append("incomplete_test_periods")
    if primary.active_periods < config.min_active_periods:
        reasons.append("too_few_active_periods")
    if primary.positive_periods < config.min_positive_periods:
        reasons.append("too_few_profitable_periods")
    if primary.average_return <= 0:
        reasons.append("average_return_not_positive")
    if primary.median_return <= 0:
        reasons.append("median_return_not_positive")
    if primary.compounded_return <= 0:
        reasons.append("compounded_return_not_positive")
    if primary.early_average_return <= 0:
        reasons.append("early_half_not_positive")
    if primary.late_average_return <= 0:
        reasons.append("late_half_not_positive")
    if primary.average_stressed_return <= 0:
        reasons.append("extra_cost_stressed_return_not_positive")
    if primary.average_return <= price_only.average_return:
        reasons.append("does_not_improve_price_only_ablation")
    if primary.average_return <= simple.average_return:
        reasons.append("does_not_improve_simple_trend_baseline")
    if primary_beats_price < 0.50:
        reasons.append("does_not_beat_price_only_often_enough")
    if primary_beats_simple < 0.50:
        reasons.append("does_not_beat_simple_trend_often_enough")
    if primary.worst_drawdown > config.max_portfolio_drawdown:
        reasons.append("drawdown_too_high")
    if positive_multifactor < config.min_positive_multifactor_variants:
        reasons.append("too_few_positive_multifactor_variants")
    if leave_positive < config.min_leave_one_out_positive_fraction:
        reasons.append("leave_one_asset_out_not_robust")
    accepted = not reasons
    first = histories[next(iter(histories))][0].timestamp.isoformat()
    last = histories[next(iter(histories))][-1].timestamp.isoformat()
    return CryptoMultiFactorReport(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        market=market.value,
        symbols=sorted(histories),
        dataset_fingerprint=dataset_fingerprint(histories),
        dataset_start=first,
        dataset_end=last,
        config=asdict(config),
        factor_weights={"full": FULL_FACTOR_WEIGHTS, "price_only": PRICE_FACTOR_WEIGHTS},
        variants=summaries,
        primary_beats_price_only_fraction=primary_beats_price,
        primary_beats_simple_trend_fraction=primary_beats_simple,
        leave_one_out_average_returns=leave_one_out,
        leave_one_out_positive_fraction=leave_positive,
        accepted=accepted,
        eligible_for_forward_paper=accepted,
        reasons=reasons,
    )


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(destination)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the frozen crypto multifactor portfolio")
    parser.add_argument("--folder", required=True)
    parser.add_argument("--market", choices=[market.value for market in Market], default=Market.CRYPTO.value)
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args(argv)
    report = evaluate_crypto_multifactor(args.folder, Market(args.market))
    payload = asdict(report)
    _write_json(args.json_out, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
