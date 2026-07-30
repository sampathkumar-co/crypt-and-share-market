from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any, Iterable

from tradebot.backtest.metrics import max_drawdown
from tradebot.backtest.research_gate import dataset_fingerprint, load_histories
from tradebot.data.crypto_4h_provider import (
    EXPECTED_END_EXCLUSIVE,
    EXPECTED_FOUR_HOUR_BARS,
    EXPECTED_START,
    REQUIRED_SYMBOLS,
)
from tradebot.data.crypto_external_factors import (
    ExternalFactorDataError,
    sha256_file,
    verify_external_manifest,
)
from tradebot.models import Candle, Market
from tradebot.risk.cost_engine import CostEngine
from tradebot.risk.tax_engine import TaxEngine

SCHEMA_VERSION = "1.4"
SLEEVES = ("trend", "range", "funding")
PRIORITY = {"funding": 3, "trend": 2, "range": 1}


@dataclass(frozen=True)
class MultiRegimeVariant:
    name: str
    enabled_sleeves: tuple[str, ...]


VARIANTS = (
    MultiRegimeVariant("primary_multiregime", SLEEVES),
    MultiRegimeVariant("without_trend", ("range", "funding")),
    MultiRegimeVariant("without_range", ("trend", "funding")),
    MultiRegimeVariant("without_funding", ("trend", "range")),
    MultiRegimeVariant("trend_only", ("trend",)),
    MultiRegimeVariant("range_only", ("range",)),
    MultiRegimeVariant("funding_only", ("funding",)),
)


@dataclass(frozen=True)
class MultiRegimeConfig:
    total_bars: int = 5_400
    warmup_bars: int = 600
    discovery_periods: int = 8
    discovery_test_bars: int = 480
    embargo_bars: int = 240
    holdout_bars: int = 720
    holdout_periods: int = 3
    holdout_test_bars: int = 240
    max_positions: int = 3
    max_asset_weight: float = 0.25
    min_cash_reserve: float = 0.25
    target_volatility: float = 0.30
    min_trade_weight: float = 0.04
    extra_cost_per_turnover: float = 0.0015
    starting_cash: float = 100_000.0
    max_drawdown: float = 0.15

    def __post_init__(self) -> None:
        discovery_used = self.warmup_bars + self.discovery_periods * self.discovery_test_bars
        if discovery_used + self.embargo_bars + self.holdout_bars != self.total_bars:
            raise ValueError("v1.4 split must consume exactly 5,400 bars")
        if self.holdout_periods * self.holdout_test_bars != self.holdout_bars:
            raise ValueError("v1.4 holdout must be three 240-bar periods")
        if self.max_positions * self.max_asset_weight > 1.0 - self.min_cash_reserve + 1e-12:
            raise ValueError("position caps exceed the frozen gross-exposure limit")
        if self.starting_cash <= 0:
            raise ValueError("starting cash must be positive")

    @property
    def discovery_end_exclusive(self) -> int:
        return self.warmup_bars + self.discovery_periods * self.discovery_test_bars

    @property
    def embargo_start(self) -> int:
        return self.discovery_end_exclusive

    @property
    def holdout_start(self) -> int:
        return self.discovery_end_exclusive + self.embargo_bars


@dataclass(frozen=True)
class Candidate:
    symbol: str
    sleeve: str
    strength: float
    atr: float


@dataclass
class PositionState:
    quantity: float
    average_price: float
    entry_time: datetime
    entry_index: int
    sleeve: str
    entry_atr: float
    highest_close: float


@dataclass
class MultiRegimePeriod:
    variant: str
    mode: str
    period: int
    test_start: str
    test_end: str
    net_return: float
    stressed_return: float
    equal_weight_buy_hold_return: float
    excess_vs_equal_weight: float
    max_drawdown: float
    turnover: float
    transactions: int
    active: bool
    average_cash_weight: float
    selected_symbols: list[str]
    sleeve_entries: dict[str, int]
    traded_notional_by_asset: dict[str, float]
    total_fees: float
    total_slippage: float
    total_tax: float


@dataclass
class MultiRegimeSummary:
    variant: str
    periods: list[MultiRegimePeriod]
    active_periods: int
    positive_periods: int
    average_return: float
    median_return: float
    compounded_return: float
    average_stressed_return: float
    first_half_average: float
    second_half_average: float
    average_equal_weight_return: float
    average_excess_vs_equal_weight: float
    beat_equal_weight_fraction: float
    worst_drawdown: float
    average_turnover: float
    selected_symbols: list[str]
    sleeve_entries: dict[str, int]
    traded_notional_by_asset: dict[str, float]
    max_asset_notional_fraction: float


@dataclass
class MultiRegimeReport:
    schema_version: str
    generated_at: str
    mode: str
    market: str
    symbols: list[str]
    price_dataset_fingerprint: str
    external_manifest_fingerprint: str
    dataset_start: str
    dataset_end: str
    discovery_test_start: str
    discovery_test_end: str
    embargo_start: str
    embargo_end: str
    holdout_start: str
    holdout_end: str
    config: dict[str, Any]
    variants: list[MultiRegimeSummary]
    primary_beats_trend_periods: int
    positive_sleeve_ablations: int
    leave_one_asset_out_average_returns: dict[str, float]
    leave_one_asset_out_positive_count: int
    accepted: bool
    eligible_for_holdout: bool
    eligible_for_shadow_paper: bool
    eligible_for_forward_paper: bool
    reasons: list[str]
    paper_only: bool = True
    authorizes_real_trading: bool = False


@dataclass(frozen=True)
class ExternalStore:
    stablecoin: dict[str, dict[date, float]]
    funding: dict[str, dict[datetime, float]]
    macro: dict[str, dict[date, float]]
    manifest: dict[str, Any]
    manifest_fingerprint: str


def _read_numeric_csv(path: Path, date_column: str, value_column: str) -> dict[date, float]:
    output: dict[date, float] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                row_date = date.fromisoformat(row[date_column][:10])
                value = float(row[value_column])
            except (KeyError, TypeError, ValueError):
                continue
            output[row_date] = value
    return output


def _four_hour_bucket(timestamp: datetime) -> datetime:
    clean = timestamp.replace(tzinfo=None, minute=0, second=0, microsecond=0)
    return clean.replace(hour=(clean.hour // 4) * 4)


def _read_funding_four_hour(path: Path) -> dict[datetime, float]:
    grouped: dict[datetime, list[float]] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                stamp = datetime.fromisoformat(row["timestamp"]).replace(tzinfo=None)
                value = float(row["funding_rate"])
            except (KeyError, TypeError, ValueError):
                continue
            grouped.setdefault(_four_hour_bucket(stamp), []).append(value)
    return {stamp: mean(values) for stamp, values in grouped.items() if values}


def load_external_store(root: str | Path) -> ExternalStore:
    base = Path(root)
    manifest = verify_external_manifest(base)
    providers = {item["provider"] for item in manifest.get("files", [])}
    expected = {"coinmetrics-community-archive", "hyperliquid-public-info", "fred-public-csv"}
    if providers != expected:
        raise ExternalFactorDataError(f"Unexpected v1.4 provider set: {sorted(providers)}")
    stablecoin = {
        asset: _read_numeric_csv(base / "coinmetrics" / f"{asset}.csv", "date", "CapMrktCurUSD")
        for asset in ("usdt", "usdc")
    }
    funding = {
        symbol: _read_funding_four_hour(base / "hyperliquid" / f"{symbol}.csv")
        for symbol in REQUIRED_SYMBOLS
    }
    macro = {
        series: _read_numeric_csv(base / "fred" / f"{series}.csv", "date", "value")
        for series in ("VIXCLS", "DTWEXBGS", "DGS10")
    }
    return ExternalStore(
        stablecoin=stablecoin,
        funding=funding,
        macro=macro,
        manifest=manifest,
        manifest_fingerprint=sha256_file(base / "manifest.json"),
    )


def load_exact_histories(folder: str | Path, config: MultiRegimeConfig | None = None) -> dict[str, list[Candle]]:
    config = config or MultiRegimeConfig()
    loaded = load_histories(folder)
    missing = sorted(set(REQUIRED_SYMBOLS) - set(loaded))
    if missing:
        raise ValueError(f"Missing v1.4 histories: {', '.join(missing)}")
    common = sorted(set.intersection(*(set(item.timestamp for item in loaded[s]) for s in REQUIRED_SYMBOLS)))
    if len(common) != config.total_bars:
        raise ValueError(f"v1.4 requires exactly {config.total_bars} common four-hour candles; found {len(common)}")
    if common[0] != EXPECTED_START or common[-1] != EXPECTED_END_EXCLUSIVE - timedelta(hours=4):
        raise ValueError("v1.4 price interval changed")
    aligned: dict[str, list[Candle]] = {}
    common_set = set(common)
    for symbol in REQUIRED_SYMBOLS:
        mapping = {item.timestamp: item for item in loaded[symbol] if item.timestamp in common_set}
        aligned[symbol] = [mapping[stamp] for stamp in common]
        if len(aligned[symbol]) != config.total_bars:
            raise ValueError(f"{symbol} is incomplete after v1.4 alignment")
    return aligned


def _ema(values: Iterable[float], span: int) -> float:
    data = list(values)
    if not data:
        return 0.0
    alpha = 2.0 / (span + 1.0)
    result = data[0]
    for value in data[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _return_series(candles: list[Candle], window: int) -> list[float]:
    closes = [item.close for item in candles[-(window + 1) :]]
    return [closes[index] / closes[index - 1] - 1.0 for index in range(1, len(closes)) if closes[index - 1] > 0]


def _simple_return(candles: list[Candle], window: int) -> float:
    if len(candles) <= window:
        return 0.0
    base = candles[-1 - window].close
    return candles[-1].close / base - 1.0 if base > 0 else 0.0


def _realized_horizon_volatility(candles: list[Candle], window: int) -> float:
    values = _return_series(candles, window)
    return pstdev(values) * math.sqrt(window) if len(values) >= 2 else 0.0


def _annualized_volatility(candles: list[Candle], window: int = 60) -> float:
    values = _return_series(candles, window)
    return pstdev(values) * math.sqrt(6.0 * 365.0) if len(values) >= 2 else 0.0


def _atr(candles: list[Candle], window: int = 20) -> float:
    rows = candles[-(window + 1) :]
    if len(rows) < 2:
        return 0.0
    ranges = []
    for previous, current in zip(rows, rows[1:]):
        ranges.append(max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close)))
    return mean(ranges) if ranges else 0.0


def _rsi(candles: list[Candle], window: int = 14) -> float:
    closes = [item.close for item in candles[-(window + 1) :]]
    if len(closes) <= window:
        return 50.0
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    gains = mean(max(value, 0.0) for value in changes)
    losses = mean(max(-value, 0.0) for value in changes)
    if losses <= 1e-12:
        return 100.0 if gains > 0 else 50.0
    relative = gains / losses
    return 100.0 - 100.0 / (1.0 + relative)


def _efficiency_ratio(candles: list[Candle], window: int) -> float:
    closes = [item.close for item in candles[-(window + 1) :]]
    if len(closes) <= window:
        return 0.0
    direction = abs(closes[-1] - closes[0])
    path = sum(abs(closes[index] - closes[index - 1]) for index in range(1, len(closes)))
    return direction / path if path > 0 else 0.0


def _zscore(candles: list[Candle], window: int = 48) -> float:
    if len(candles) <= window:
        return 0.0
    history = [item.close for item in candles[-(window + 1) : -1]]
    deviation = pstdev(history)
    return (candles[-1].close - mean(history)) / deviation if deviation > 0 else 0.0


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(math.ceil(fraction * (len(ordered) - 1)))
    return ordered[index]


def _last_on_or_before(series: dict[date, float], as_of: date) -> float | None:
    values = [(row_date, value) for row_date, value in series.items() if row_date <= as_of]
    return max(values, default=(None, None), key=lambda item: item[0] or date.min)[1]


def _stablecoin_supportive(store: ExternalStore, as_of: date) -> bool:
    available = as_of - timedelta(days=1)

    def total(on_date: date) -> float | None:
        left = _last_on_or_before(store.stablecoin["usdt"], on_date)
        right = _last_on_or_before(store.stablecoin["usdc"], on_date)
        return left + right if left is not None and right is not None else None

    current = total(available)
    prior_30 = total(available - timedelta(days=30))
    prior_90 = total(available - timedelta(days=90))
    if current is None or prior_30 is None or prior_90 is None:
        return False
    return current > prior_30 and current > prior_90


def _observation_change(series: dict[date, float], as_of: date, observations: int) -> tuple[float, float] | None:
    available = as_of - timedelta(days=1)
    values = sorted((row_date, value) for row_date, value in series.items() if row_date <= available)
    if len(values) <= observations:
        return None
    return values[-1][1], values[-1 - observations][1]


def _macro_supportive(store: ExternalStore, as_of: date) -> bool:
    supportive = 0
    vix = _observation_change(store.macro["VIXCLS"], as_of, 20)
    if vix is not None and vix[1] > 0:
        supportive += int(vix[0] <= 30.0 and vix[0] / vix[1] - 1.0 <= 0.25)
    dollar = _observation_change(store.macro["DTWEXBGS"], as_of, 20)
    if dollar is not None and dollar[1] > 0:
        supportive += int(dollar[0] / dollar[1] - 1.0 < 0.02)
    ten_year = _observation_change(store.macro["DGS10"], as_of, 20)
    if ten_year is not None:
        supportive += int(ten_year[0] - ten_year[1] < 0.50)
    return supportive >= 2


def _external_risk_multiplier(store: ExternalStore, as_of: date) -> float:
    return 0.75 if not _stablecoin_supportive(store, as_of) and not _macro_supportive(store, as_of) else 1.0


def _funding_seven_day_means(store: ExternalStore, symbol: str, as_of: datetime) -> list[float]:
    values = [value for stamp, value in sorted(store.funding[symbol].items()) if stamp <= as_of]
    if len(values) < 42:
        return []
    start = max(42, len(values) - 720)
    return [mean(values[index - 42 : index]) for index in range(start, len(values) + 1)]


def _funding_snapshot(store: ExternalStore, symbol: str, as_of: datetime) -> tuple[float, float, float] | None:
    means = _funding_seven_day_means(store, symbol, as_of)
    if len(means) < 60:
        return None
    return means[-1], _percentile(means[:-1], 0.10), median(means[:-1])


def _trend_candidate(symbol: str, candles: list[Candle]) -> Candidate | None:
    if len(candles) < 180:
        return None
    closes = [item.close for item in candles]
    close = closes[-1]
    ema18 = _ema(closes, 18)
    ema36 = _ema(closes, 36)
    ema144 = _ema(closes, 144)
    atr = _atr(candles, 20)
    if not (close > ema144 and ema36 > ema144 and _simple_return(candles, 60) > 0 and _efficiency_ratio(candles, 144) >= 0.28 and atr > 0):
        return None
    touched = False
    for offset in (3, 2, 1):
        slice_end = len(candles) - offset
        if slice_end < 18:
            continue
        row = candles[slice_end]
        local_ema = _ema((item.close for item in candles[: slice_end + 1]), 18)
        if row.low <= local_ema or row.close <= local_ema:
            touched = True
            break
    pullback = touched and close > ema18 and close > candles[-2].high
    prior_high = max(item.high for item in candles[-49:-1])
    prior_volume = median(item.volume for item in candles[-49:-1])
    continuation = (
        close > prior_high
        and prior_volume > 0
        and candles[-1].volume >= 1.15 * prior_volume
        and close - ema18 <= 1.75 * atr
    )
    if not pullback and not continuation:
        return None
    atr_fraction = atr / close if close > 0 else 1.0
    pullback_strength = (_simple_return(candles, 60) + max(0.0, close / ema18 - 1.0)) / max(atr_fraction, 1e-6)
    continuation_strength = (max(0.0, close / prior_high - 1.0) / max(atr_fraction, 1e-6)) + candles[-1].volume / prior_volume - 1.0
    return Candidate(symbol, "trend", max(pullback_strength if pullback else 0.0, continuation_strength if continuation else 0.0), atr)


def _range_candidate(symbol: str, candles: list[Candle]) -> Candidate | None:
    if len(candles) < 180:
        return None
    closes = [item.close for item in candles]
    atr = _atr(candles, 20)
    if atr <= 0:
        return None
    regime = (
        _efficiency_ratio(candles, 72) <= 0.30
        and abs(_simple_return(candles, 72)) <= 1.25 * _realized_horizon_volatility(candles, 72)
        and abs(_ema(closes, 36) - _ema(closes, 144)) <= atr
    )
    z = _zscore(candles, 48)
    rsi = _rsi(candles, 14)
    if not (regime and z <= -1.50 and rsi <= 35.0 and candles[-1].close > candles[-2].close):
        return None
    return Candidate(symbol, "range", abs(z) + (35.0 - rsi) / 35.0, atr)


def _funding_candidate(symbol: str, candles: list[Candle], store: ExternalStore) -> Candidate | None:
    if len(candles) < 180:
        return None
    snapshot = _funding_snapshot(store, symbol, candles[-1].timestamp)
    if snapshot is None:
        return None
    recent, tenth, _ = snapshot
    prior_high = max(item.close for item in candles[-121:-1])
    drawdown = candles[-1].close / prior_high - 1.0 if prior_high > 0 else 0.0
    recent_low = min(item.close for item in candles[-6:]) <= min(item.close for item in candles[-20:]) + 1e-12
    closes = [item.close for item in candles]
    cross = candles[-1].close > _ema(closes, 12) and candles[-2].close <= _ema(closes[:-1], 12)
    atr = _atr(candles, 20)
    if not (recent < 0 and recent <= tenth and drawdown <= -0.15 and recent_low and cross and atr > 0):
        return None
    strength = abs(recent - tenth) / max(abs(tenth), 1e-9) + abs(drawdown)
    return Candidate(symbol, "funding", strength, atr)


def signal_candidates(
    prior: dict[str, list[Candle]],
    store: ExternalStore,
    enabled_sleeves: tuple[str, ...],
) -> dict[str, Candidate]:
    output: dict[str, Candidate] = {}
    for symbol, candles in prior.items():
        candidates: list[Candidate] = []
        if "funding" in enabled_sleeves:
            candidate = _funding_candidate(symbol, candles, store)
            if candidate is not None:
                candidates.append(candidate)
        if "trend" in enabled_sleeves:
            candidate = _trend_candidate(symbol, candles)
            if candidate is not None:
                candidates.append(candidate)
        if "range" in enabled_sleeves:
            candidate = _range_candidate(symbol, candles)
            if candidate is not None:
                candidates.append(candidate)
        if candidates:
            output[symbol] = max(candidates, key=lambda item: (PRIORITY[item.sleeve], item.strength, item.symbol))
    return output


def _position_exit(position: PositionState, candles: list[Candle], store: ExternalStore, index: int, symbol: str) -> bool:
    if len(candles) < 180:
        return False
    close = candles[-1].close
    atr = _atr(candles, 20)
    held = index - position.entry_index
    position.highest_close = max(position.highest_close, close)
    if position.sleeve == "trend":
        return close < _ema((item.close for item in candles), 36) or close < position.highest_close - 2.5 * atr or held >= 90
    if position.sleeve == "range":
        return _zscore(candles, 48) >= 0.0 or _rsi(candles, 14) >= 55.0 or close < position.average_price - 2.0 * position.entry_atr or held >= 36
    snapshot = _funding_snapshot(store, symbol, candles[-1].timestamp)
    funding_recovered = snapshot is not None and snapshot[0] >= snapshot[2]
    return funding_recovered or close >= _ema((item.close for item in candles), 72) or close < position.average_price - 2.25 * position.entry_atr or held >= 60


def _portfolio_volatility(weights: dict[str, float], prior: dict[str, list[Candle]]) -> float:
    if not weights:
        return 0.0
    series = {symbol: _return_series(prior[symbol], 60) for symbol in weights}
    count = min((len(values) for values in series.values()), default=0)
    if count < 3:
        return 0.0
    returns = [sum(weights[symbol] * series[symbol][index] for symbol in weights) for index in range(-count, 0)]
    return pstdev(returns) * math.sqrt(6.0 * 365.0)


def _cap_weights(raw: dict[str, float], exposure: float, cap: float) -> dict[str, float]:
    pending = {symbol: max(value, 0.0) for symbol, value in raw.items() if value > 0}
    output: dict[str, float] = {}
    remaining = max(0.0, exposure)
    while pending and remaining > 1e-12:
        denominator = sum(pending.values())
        if denominator <= 0:
            break
        capped: list[str] = []
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


def target_weights(
    symbols: list[str],
    prior: dict[str, list[Candle]],
    store: ExternalStore,
    drawdown_multiplier: float,
    config: MultiRegimeConfig,
) -> dict[str, float]:
    if not symbols or drawdown_multiplier <= 0:
        return {}
    raw = {symbol: 1.0 / max(_annualized_volatility(prior[symbol], 60), 0.10) for symbol in symbols}
    unit_total = sum(raw.values())
    unit = {symbol: value / unit_total for symbol, value in raw.items()}
    estimated = _portfolio_volatility(unit, prior)
    gross = (1.0 - config.min_cash_reserve) * drawdown_multiplier
    as_of = prior[next(iter(prior))][-1].timestamp.date()
    gross *= _external_risk_multiplier(store, as_of)
    if estimated > 0:
        gross = min(gross, config.target_volatility / estimated)
    return _cap_weights(raw, gross, config.max_asset_weight)


def _period_bounds(period: int, config: MultiRegimeConfig, mode: str) -> tuple[int, int]:
    if mode == "discovery":
        if not 1 <= period <= config.discovery_periods:
            raise ValueError("discovery period outside frozen range")
        start = config.warmup_bars + (period - 1) * config.discovery_test_bars
        return start, start + config.discovery_test_bars - 1
    if mode == "holdout":
        if not 1 <= period <= config.holdout_periods:
            raise ValueError("holdout period outside frozen range")
        start = config.holdout_start + (period - 1) * config.holdout_test_bars
        return start, start + config.holdout_test_bars - 1
    raise ValueError("mode must be discovery or holdout")


def _benchmark_return(histories: dict[str, list[Candle]], start: int, end: int) -> float:
    values = []
    for candles in histories.values():
        first = candles[start].open
        values.append(candles[end].close / first - 1.0 if first > 0 else 0.0)
    return mean(values) if values else 0.0


def _drawdown_multiplier(current: float) -> float:
    if current >= 0.15:
        return 0.0
    if current >= 0.10:
        return 0.25
    if current >= 0.05:
        return 0.65
    return 1.0


def _simulate_period(
    histories: dict[str, list[Candle]],
    store: ExternalStore,
    variant: MultiRegimeVariant,
    period: int,
    config: MultiRegimeConfig,
    mode: str,
) -> MultiRegimePeriod:
    start, end = _period_bounds(period, config, mode)
    cash = config.starting_cash
    positions: dict[str, PositionState] = {}
    costs = CostEngine()
    taxes = TaxEngine()
    side_rate = costs.config.crypto_exchange_fee_pct + costs.config.crypto_slippage_pct
    total_fees = total_slippage = total_tax = total_turnover = 0.0
    transactions = 0
    selected_symbols: set[str] = set()
    sleeve_entries = {sleeve: 0 for sleeve in SLEEVES}
    traded_notional = {symbol: 0.0 for symbol in histories}
    curve = [cash]
    cash_weights: list[float] = []
    peak_equity = cash

    def mark(index: int, field: str) -> float:
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
        traded_notional[symbol] += notional
        transactions += 1
        remaining = position.quantity - quantity
        if remaining <= 1e-10:
            positions.pop(symbol)
        else:
            position.quantity = remaining

    def buy(candidate: Candidate, notional: float, price: float, timestamp: datetime, index: int) -> None:
        nonlocal cash, total_fees, total_slippage, total_turnover, transactions
        affordable = cash / (1.0 + side_rate)
        notional = min(notional, affordable)
        if notional <= 1e-8 or price <= 0:
            return
        fee = notional * costs.config.crypto_exchange_fee_pct
        slippage = notional * costs.config.crypto_slippage_pct
        quantity = notional / price
        cash -= notional + fee + slippage
        total_fees += fee
        total_slippage += slippage
        total_turnover += notional
        traded_notional[candidate.symbol] += notional
        transactions += 1
        current = positions.get(candidate.symbol)
        if current is None:
            positions[candidate.symbol] = PositionState(
                quantity=quantity,
                average_price=price,
                entry_time=timestamp,
                entry_index=index,
                sleeve=candidate.sleeve,
                entry_atr=candidate.atr,
                highest_close=price,
            )
            sleeve_entries[candidate.sleeve] += 1
            selected_symbols.add(candidate.symbol)
        else:
            combined = current.quantity + quantity
            current.average_price = (current.average_price * current.quantity + price * quantity) / combined
            current.quantity = combined

    for index in range(start, end + 1):
        timestamp = histories[next(iter(histories))][index].timestamp
        prior = {symbol: candles[:index] for symbol, candles in histories.items()}
        if min(len(candles) for candles in prior.values()) < config.warmup_bars:
            raise ValueError("v1.4 simulation attempted to trade without frozen warm-up")
        equity_open = mark(index, "open")
        current_drawdown = max(0.0, 1.0 - equity_open / peak_equity) if peak_equity > 0 else 0.0
        exited: set[str] = set()
        for symbol in list(positions):
            if _position_exit(positions[symbol], prior[symbol], store, index, symbol):
                sell(symbol, positions[symbol].quantity, histories[symbol][index].open, timestamp)
                exited.add(symbol)

        candidates = signal_candidates(prior, store, variant.enabled_sleeves)
        available = [candidate for symbol, candidate in candidates.items() if symbol not in positions and symbol not in exited]
        available.sort(key=lambda item: (PRIORITY[item.sleeve], item.strength, item.symbol), reverse=True)
        desired = list(positions)
        chosen_candidates: dict[str, Candidate] = {}
        for candidate in available:
            if len(desired) >= config.max_positions:
                break
            desired.append(candidate.symbol)
            chosen_candidates[candidate.symbol] = candidate

        weights = target_weights(desired, prior, store, _drawdown_multiplier(current_drawdown), config)
        current_values = {symbol: position.quantity * histories[symbol][index].open for symbol, position in positions.items()}
        all_symbols = set(current_values) | set(weights)
        for symbol in sorted(all_symbols):
            target = equity_open * weights.get(symbol, 0.0)
            current_value = current_values.get(symbol, 0.0)
            difference = current_value - target
            if difference > config.min_trade_weight * equity_open and symbol in positions:
                sell(symbol, difference / histories[symbol][index].open, histories[symbol][index].open, timestamp)

        equity_after_sales = mark(index, "open")
        for symbol in sorted(weights, key=lambda item: (weights[item], item), reverse=True):
            target = equity_after_sales * weights[symbol]
            current_value = positions[symbol].quantity * histories[symbol][index].open if symbol in positions else 0.0
            difference = target - current_value
            if difference > config.min_trade_weight * equity_after_sales:
                candidate = chosen_candidates.get(symbol)
                if candidate is None and symbol in positions:
                    candidate = Candidate(symbol, positions[symbol].sleeve, 0.0, positions[symbol].entry_atr)
                if candidate is not None:
                    buy(candidate, difference, histories[symbol][index].open, timestamp, index)

        equity_close = mark(index, "close")
        peak_equity = max(peak_equity, equity_close)
        curve.append(equity_close)
        cash_weights.append(cash / equity_close if equity_close > 0 else 1.0)

    final_time = histories[next(iter(histories))][end].timestamp
    for symbol in list(positions):
        sell(symbol, positions[symbol].quantity, histories[symbol][end].close, final_time)
    curve.append(cash)
    benchmark = _benchmark_return(histories, start, end)
    net_return = cash / config.starting_cash - 1.0
    turnover = total_turnover / config.starting_cash
    return MultiRegimePeriod(
        variant=variant.name,
        mode=mode,
        period=period,
        test_start=histories[next(iter(histories))][start].timestamp.isoformat(),
        test_end=histories[next(iter(histories))][end].timestamp.isoformat(),
        net_return=net_return,
        stressed_return=net_return - turnover * config.extra_cost_per_turnover,
        equal_weight_buy_hold_return=benchmark,
        excess_vs_equal_weight=net_return - benchmark,
        max_drawdown=max_drawdown(curve),
        turnover=turnover,
        transactions=transactions,
        active=transactions > 0,
        average_cash_weight=mean(cash_weights) if cash_weights else 1.0,
        selected_symbols=sorted(selected_symbols),
        sleeve_entries=sleeve_entries,
        traded_notional_by_asset={symbol: value for symbol, value in traded_notional.items() if value > 0},
        total_fees=total_fees,
        total_slippage=total_slippage,
        total_tax=total_tax,
    )


def _pseudo_period(histories: dict[str, list[Candle]], period: int, config: MultiRegimeConfig, mode: str, variant: str) -> MultiRegimePeriod:
    start, end = _period_bounds(period, config, mode)
    benchmark = _benchmark_return(histories, start, end)
    value = benchmark if variant == "equal_weight_buy_hold" else 0.0
    return MultiRegimePeriod(
        variant=variant,
        mode=mode,
        period=period,
        test_start=histories[next(iter(histories))][start].timestamp.isoformat(),
        test_end=histories[next(iter(histories))][end].timestamp.isoformat(),
        net_return=value,
        stressed_return=value,
        equal_weight_buy_hold_return=benchmark,
        excess_vs_equal_weight=value - benchmark,
        max_drawdown=0.0,
        turnover=0.0,
        transactions=0,
        active=variant == "equal_weight_buy_hold",
        average_cash_weight=1.0 if variant == "cash" else 0.0,
        selected_symbols=[] if variant == "cash" else sorted(histories),
        sleeve_entries={sleeve: 0 for sleeve in SLEEVES},
        traded_notional_by_asset={},
        total_fees=0.0,
        total_slippage=0.0,
        total_tax=0.0,
    )


def _summarize(variant: str, periods: list[MultiRegimePeriod]) -> MultiRegimeSummary:
    returns = [item.net_return for item in periods]
    split = len(returns) // 2
    traded: dict[str, float] = {}
    entries = {sleeve: 0 for sleeve in SLEEVES}
    selected: set[str] = set()
    for period in periods:
        selected.update(period.selected_symbols)
        for sleeve, count in period.sleeve_entries.items():
            entries[sleeve] += count
        for symbol, value in period.traded_notional_by_asset.items():
            traded[symbol] = traded.get(symbol, 0.0) + value
    total_notional = sum(traded.values())
    concentration = max(traded.values(), default=0.0) / total_notional if total_notional > 0 else 0.0
    return MultiRegimeSummary(
        variant=variant,
        periods=periods,
        active_periods=sum(item.active for item in periods),
        positive_periods=sum(value > 0 for value in returns),
        average_return=mean(returns) if returns else 0.0,
        median_return=median(returns) if returns else 0.0,
        compounded_return=math.prod(1.0 + value for value in returns) - 1.0 if returns else 0.0,
        average_stressed_return=mean(item.stressed_return for item in periods) if periods else 0.0,
        first_half_average=mean(returns[:split]) if split else 0.0,
        second_half_average=mean(returns[split:]) if returns[split:] else 0.0,
        average_equal_weight_return=mean(item.equal_weight_buy_hold_return for item in periods) if periods else 0.0,
        average_excess_vs_equal_weight=mean(item.excess_vs_equal_weight for item in periods) if periods else 0.0,
        beat_equal_weight_fraction=sum(item.net_return > item.equal_weight_buy_hold_return for item in periods) / len(periods) if periods else 0.0,
        worst_drawdown=max((item.max_drawdown for item in periods), default=0.0),
        average_turnover=mean(item.turnover for item in periods) if periods else 0.0,
        selected_symbols=sorted(selected),
        sleeve_entries=entries,
        traded_notional_by_asset=traded,
        max_asset_notional_fraction=concentration,
    )


def _variant_summary(histories: dict[str, list[Candle]], store: ExternalStore, variant: MultiRegimeVariant, config: MultiRegimeConfig, mode: str) -> MultiRegimeSummary:
    count = config.discovery_periods if mode == "discovery" else config.holdout_periods
    periods = [_simulate_period(histories, store, variant, period, config, mode) for period in range(1, count + 1)]
    return _summarize(variant.name, periods)


def _pseudo_summary(histories: dict[str, list[Candle]], config: MultiRegimeConfig, mode: str, variant: str) -> MultiRegimeSummary:
    count = config.discovery_periods if mode == "discovery" else config.holdout_periods
    return _summarize(variant, [_pseudo_period(histories, period, config, mode, variant) for period in range(1, count + 1)])


def _date_boundaries(histories: dict[str, list[Candle]], config: MultiRegimeConfig) -> dict[str, str]:
    candles = histories[next(iter(histories))]
    discovery_start, _ = _period_bounds(1, config, "discovery")
    _, discovery_end = _period_bounds(config.discovery_periods, config, "discovery")
    return {
        "discovery_test_start": candles[discovery_start].timestamp.isoformat(),
        "discovery_test_end": candles[discovery_end].timestamp.isoformat(),
        "embargo_start": candles[config.embargo_start].timestamp.isoformat(),
        "embargo_end": candles[config.holdout_start - 1].timestamp.isoformat(),
        "holdout_start": candles[config.holdout_start].timestamp.isoformat(),
        "holdout_end": candles[-1].timestamp.isoformat(),
    }


def evaluate_discovery(
    price_folder: str | Path,
    external_folder: str | Path,
    market: Market = Market.CRYPTO,
    config: MultiRegimeConfig | None = None,
) -> MultiRegimeReport:
    if market != Market.CRYPTO:
        raise ValueError("v1.4 is frozen to crypto")
    config = config or MultiRegimeConfig()
    histories = load_exact_histories(price_folder, config)
    store = load_external_store(external_folder)
    summaries = [_variant_summary(histories, store, variant, config, "discovery") for variant in VARIANTS]
    summaries.extend((_pseudo_summary(histories, config, "discovery", "cash"), _pseudo_summary(histories, config, "discovery", "equal_weight_buy_hold")))
    by_name = {item.variant: item for item in summaries}
    primary = by_name["primary_multiregime"]
    trend = by_name["trend_only"]
    equal_weight = by_name["equal_weight_buy_hold"]
    beats_trend = sum(left.net_return > right.net_return for left, right in zip(primary.periods, trend.periods))
    ablation_positive = sum(by_name[name].average_return > 0 for name in ("without_trend", "without_range", "without_funding"))
    leave_one_out: dict[str, float] = {}
    primary_variant = VARIANTS[0]
    for omitted in REQUIRED_SYMBOLS:
        subset = {symbol: candles for symbol, candles in histories.items() if symbol != omitted}
        leave_one_out[omitted] = _variant_summary(subset, store, primary_variant, config, "discovery").average_return
    leave_positive = sum(value > 0 for value in leave_one_out.values())

    reasons: list[str] = []
    if len(primary.periods) != config.discovery_periods:
        reasons.append("incomplete_discovery_periods")
    if primary.active_periods < 6:
        reasons.append("too_few_active_periods")
    if primary.positive_periods < 5:
        reasons.append("too_few_profitable_periods")
    if primary.average_return <= 0:
        reasons.append("average_return_not_positive")
    if primary.median_return <= 0:
        reasons.append("median_return_not_positive")
    if primary.compounded_return <= 0:
        reasons.append("compounded_return_not_positive")
    if primary.average_stressed_return <= 0:
        reasons.append("extra_cost_stressed_return_not_positive")
    if primary.first_half_average <= 0:
        reasons.append("first_half_not_positive")
    if primary.second_half_average <= 0:
        reasons.append("second_half_not_positive")
    if primary.average_return <= equal_weight.average_return:
        reasons.append("does_not_beat_equal_weight_average")
    if primary.average_return <= trend.average_return:
        reasons.append("does_not_beat_trend_only_average")
    if beats_trend < 5:
        reasons.append("does_not_beat_trend_often_enough")
    if primary.worst_drawdown > config.max_drawdown:
        reasons.append("drawdown_too_high")
    if len(primary.selected_symbols) < 5:
        reasons.append("too_few_distinct_assets")
    if primary.max_asset_notional_fraction > 0.35:
        reasons.append("asset_notional_concentration_too_high")
    if ablation_positive < 2:
        reasons.append("sleeve_ablations_not_robust")
    if leave_positive < 6:
        reasons.append("leave_one_asset_out_not_robust")
    accepted = not reasons
    bounds = _date_boundaries(histories, config)
    return MultiRegimeReport(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        mode="discovery",
        market=market.value,
        symbols=sorted(histories),
        price_dataset_fingerprint=dataset_fingerprint(histories),
        external_manifest_fingerprint=store.manifest_fingerprint,
        dataset_start=histories[next(iter(histories))][0].timestamp.isoformat(),
        dataset_end=histories[next(iter(histories))][-1].timestamp.isoformat(),
        config=asdict(config),
        variants=summaries,
        primary_beats_trend_periods=beats_trend,
        positive_sleeve_ablations=ablation_positive,
        leave_one_asset_out_average_returns=leave_one_out,
        leave_one_asset_out_positive_count=leave_positive,
        accepted=accepted,
        eligible_for_holdout=accepted,
        eligible_for_shadow_paper=False,
        eligible_for_forward_paper=False,
        reasons=reasons,
        **bounds,
    )


def evaluate_holdout(
    price_folder: str | Path,
    external_folder: str | Path,
    discovery_json: str | Path,
    market: Market = Market.CRYPTO,
    config: MultiRegimeConfig | None = None,
) -> MultiRegimeReport:
    config = config or MultiRegimeConfig()
    discovery = json.loads(Path(discovery_json).read_text(encoding="utf-8"))
    if discovery.get("accepted") is not True or discovery.get("eligible_for_holdout") is not True:
        raise ValueError("v1.4 holdout is locked because discovery did not pass")
    histories = load_exact_histories(price_folder, config)
    store = load_external_store(external_folder)
    if discovery.get("price_dataset_fingerprint") != dataset_fingerprint(histories):
        raise ValueError("v1.4 holdout price fingerprint changed")
    if discovery.get("external_manifest_fingerprint") != store.manifest_fingerprint:
        raise ValueError("v1.4 holdout external fingerprint changed")
    summaries = [_variant_summary(histories, store, variant, config, "holdout") for variant in VARIANTS]
    summaries.extend((_pseudo_summary(histories, config, "holdout", "cash"), _pseudo_summary(histories, config, "holdout", "equal_weight_buy_hold")))
    by_name = {item.variant: item for item in summaries}
    primary = by_name["primary_multiregime"]
    trend = by_name["trend_only"]
    equal_weight = by_name["equal_weight_buy_hold"]
    reasons: list[str] = []
    if primary.active_periods < 2:
        reasons.append("too_few_active_holdout_periods")
    if primary.positive_periods < 2:
        reasons.append("too_few_profitable_holdout_periods")
    if primary.average_return <= 0:
        reasons.append("holdout_average_not_positive")
    if primary.compounded_return <= 0:
        reasons.append("holdout_compounded_not_positive")
    if primary.average_stressed_return <= 0:
        reasons.append("holdout_stressed_not_positive")
    if primary.average_return <= trend.average_return:
        reasons.append("holdout_does_not_beat_trend")
    if primary.average_return <= equal_weight.average_return:
        reasons.append("holdout_does_not_beat_equal_weight")
    if primary.worst_drawdown > config.max_drawdown:
        reasons.append("holdout_drawdown_too_high")
    if len(primary.selected_symbols) < 3:
        reasons.append("holdout_too_few_assets")
    if primary.max_asset_notional_fraction > 0.45:
        reasons.append("holdout_asset_concentration_too_high")
    accepted = not reasons
    bounds = _date_boundaries(histories, config)
    return MultiRegimeReport(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        mode="holdout",
        market=market.value,
        symbols=sorted(histories),
        price_dataset_fingerprint=dataset_fingerprint(histories),
        external_manifest_fingerprint=store.manifest_fingerprint,
        dataset_start=histories[next(iter(histories))][0].timestamp.isoformat(),
        dataset_end=histories[next(iter(histories))][-1].timestamp.isoformat(),
        config=asdict(config),
        variants=summaries,
        primary_beats_trend_periods=sum(left.net_return > right.net_return for left, right in zip(primary.periods, trend.periods)),
        positive_sleeve_ablations=sum(by_name[name].average_return > 0 for name in ("without_trend", "without_range", "without_funding")),
        leave_one_asset_out_average_returns={},
        leave_one_asset_out_positive_count=0,
        accepted=accepted,
        eligible_for_holdout=False,
        eligible_for_shadow_paper=accepted,
        eligible_for_forward_paper=False,
        reasons=reasons,
        **bounds,
    )


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(destination)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate frozen v1.4 multi-regime four-hour crypto research")
    parser.add_argument("--price-folder", required=True)
    parser.add_argument("--external-folder", required=True)
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--mode", choices=("discovery", "holdout"), default="discovery")
    parser.add_argument("--discovery-json")
    args = parser.parse_args(argv)
    if args.mode == "holdout":
        if not args.discovery_json:
            raise SystemExit("--discovery-json is required for holdout mode")
        report = evaluate_holdout(args.price_folder, args.external_folder, args.discovery_json)
    else:
        report = evaluate_discovery(args.price_folder, args.external_folder)
    payload = asdict(report)
    _write_json(args.json_out, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
