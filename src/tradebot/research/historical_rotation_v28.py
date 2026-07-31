from __future__ import annotations

import argparse
import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median, pstdev
from typing import Any

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import historical_proxy_screen_v25 as v25

MODE = "HISTORICAL_REGIME_ADAPTIVE_ROTATION_ONLY"
SCHEMA_VERSION = "2.8-regime-adaptive-rotation"
PROTOCOL_PATH = Path("research/V28_REGIME_ADAPTIVE_ROTATION_PROTOCOL.md")
ASSETS = ("BTC", "ETH", "SOL", "AVAX", "LINK", "DOGE")
SYMBOLS = {asset: f"{asset}USDT" for asset in ASSETS}
BASE_URL = "https://data.binance.vision/data"
DATA_START = datetime(2021, 1, 1, tzinfo=timezone.utc)
DISCOVERY_END = datetime(2023, 9, 30, tzinfo=timezone.utc)
VALIDATION_START = datetime(2023, 10, 1, tzinfo=timezone.utc)
VALIDATION_END = datetime(2024, 12, 31, tzinfo=timezone.utc)
EXIT_DATE = datetime(2025, 1, 1, tzinfo=timezone.utc)
WARMUP_DAYS = 200
DISCOVERY_BLOCK_DAYS = 90
STANDARD_COST = 0.002
STRESS_COST = 0.004
MAX_EXPOSURE = 0.30
NEUTRAL_EXPOSURE = 0.15


class HistoricalRotationV28Error(RuntimeError):
    """Raised when v2.8 cannot be reproduced safely."""


@dataclass(frozen=True)
class ModelSpec:
    sma_length: int
    breadth_floor: float
    rebalance_days: int
    top_n: int
    recovery_threshold: float

    @property
    def model_id(self) -> str:
        breadth = int(round(self.breadth_floor * 100))
        recovery = int(round(abs(self.recovery_threshold) * 100))
        return (
            f"sma{self.sma_length}-breadth{breadth}-rebalance{self.rebalance_days}"
            f"-top{self.top_n}-recovery{recovery}"
        )


MODEL_GRID = tuple(
    ModelSpec(sma, breadth, cadence, top_n, recovery)
    for sma in (80, 120)
    for breadth in (0.40, 0.60)
    for cadence in (5, 7)
    for top_n in (1, 2)
    for recovery in (-0.06, -0.10)
)


@dataclass(frozen=True)
class VerificationWindow:
    name: str
    start: datetime
    end: datetime


VALIDATION_WINDOWS = (
    VerificationWindow("2023-Q4", datetime(2023, 10, 1, tzinfo=timezone.utc), datetime(2023, 12, 31, tzinfo=timezone.utc)),
    VerificationWindow("2024-Q1", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 3, 31, tzinfo=timezone.utc)),
    VerificationWindow("2024-Q2", datetime(2024, 4, 1, tzinfo=timezone.utc), datetime(2024, 6, 30, tzinfo=timezone.utc)),
    VerificationWindow("2024-Q3", datetime(2024, 7, 1, tzinfo=timezone.utc), datetime(2024, 9, 30, tzinfo=timezone.utc)),
    VerificationWindow("2024-Q4", datetime(2024, 10, 1, tzinfo=timezone.utc), datetime(2024, 12, 31, tzinfo=timezone.utc)),
)


@dataclass(frozen=True)
class AssetFeatures:
    return_1: float
    return_3: float
    return_5: float
    return_20: float
    return_60: float
    return_120: float
    volatility_20: float
    sma_50: float
    sma_80: float
    sma_100: float
    sma_120: float
    close: float
    close_location: float
    volume_ratio: float
    trend_score: float


@dataclass
class SimulationResult:
    net_return: float
    gross_return: float
    maximum_drawdown: float
    turnover: float
    non_cash_action_days: int
    selected_assets: list[str]
    active_sleeves: list[str]
    daily_returns: list[float]
    asset_contribution: dict[str, float]
    sleeve_contribution: dict[str, float]


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _month_strings(start: datetime, end: datetime) -> list[str]:
    current = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    finish = datetime(end.year, end.month, 1, tzinfo=timezone.utc)
    result: list[str] = []
    while current <= finish:
        result.append(current.strftime("%Y-%m"))
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return result


def _archive_requests() -> dict[str, str]:
    requests: dict[str, str] = {}
    for asset, symbol in SYMBOLS.items():
        for month in _month_strings(DATA_START, VALIDATION_END):
            requests[f"monthly:{asset}:{month}"] = (
                f"{BASE_URL}/spot/monthly/klines/{symbol}/1d/{symbol}-1d-{month}.zip"
            )
        requests[f"exit:{asset}:2025-01-01"] = (
            f"{BASE_URL}/spot/daily/klines/{symbol}/1d/{symbol}-1d-2025-01-01.zip"
        )
    return requests


def download_inputs(max_workers: int = 16) -> tuple[dict[str, v25.DownloadedArchive], list[dict[str, str]]]:
    requests = _archive_requests()
    downloaded: dict[str, v25.DownloadedArchive] = {}
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        pending = {executor.submit(v25._download, url): (key, url) for key, url in requests.items()}
        for future in as_completed(pending):
            key, url = pending[future]
            try:
                downloaded[key] = future.result()
            except v25.HistoricalProxyScreenError as exc:
                failures.append({"key": key, "url": url, "reason": str(exc)})
    if failures:
        sample = "; ".join(item["key"] for item in sorted(failures, key=lambda row: row["key"])[:8])
        raise HistoricalRotationV28Error(f"{len(failures)} required archives failed: {sample}")
    inventory = [
        {"key": key, "url": archive.url, "sha256": archive.sha256}
        for key, archive in sorted(downloaded.items())
    ]
    return downloaded, inventory


def assemble_bars(downloaded: dict[str, v25.DownloadedArchive]) -> tuple[dict[str, dict[datetime, v25.HourlyBar]], list[datetime]]:
    bars: dict[str, dict[datetime, v25.HourlyBar]] = {asset: {} for asset in ASSETS}
    for key, archive in sorted(downloaded.items()):
        parts = key.split(":")
        asset = parts[1]
        parsed = v25._parse_klines(archive)
        for timestamp, bar in parsed.items():
            day = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
            prior = bars[asset].get(day)
            if prior is not None and prior != bar:
                raise HistoricalRotationV28Error(f"Conflicting daily bar: {asset} {day}")
            bars[asset][day] = bar
    expected: list[datetime] = []
    current = DATA_START
    while current <= EXIT_DATE:
        expected.append(current)
        current += timedelta(days=1)
    for asset in ASSETS:
        missing = [day for day in expected if day not in bars[asset]]
        if missing:
            raise HistoricalRotationV28Error(
                f"{asset} is missing {len(missing)} required days; first={_utc(missing[0])}"
            )
    return bars, expected


def _return(closes: list[float], index: int, lag: int) -> float:
    prior = closes[index - lag]
    return closes[index] / prior - 1.0 if prior > 0 else 0.0


def _sma(closes: list[float], index: int, length: int) -> float:
    values = closes[index - length + 1 : index + 1]
    if len(values) != length:
        raise HistoricalRotationV28Error(f"SMA{length} history unavailable at index {index}")
    return sum(values) / length


def _volatility(closes: list[float], index: int, length: int = 20) -> float:
    values = closes[index - length : index + 1]
    returns = [values[offset] / values[offset - 1] - 1.0 for offset in range(1, len(values))]
    return 0.0 if len(returns) < 2 else pstdev(returns)


def build_features(
    bars: dict[str, dict[datetime, v25.HourlyBar]],
    dates: list[datetime],
) -> dict[datetime, dict[str, AssetFeatures]]:
    closes = {asset: [bars[asset][day].close for day in dates] for asset in ASSETS}
    features: dict[datetime, dict[str, AssetFeatures]] = {}
    for index in range(120, len(dates)):
        day = dates[index]
        payload: dict[str, AssetFeatures] = {}
        for asset in ASSETS:
            series = closes[asset]
            bar = bars[asset][day]
            prior_volumes = [bars[asset][dates[offset]].quote_volume for offset in range(index - 20, index)]
            volume_median = median(prior_volumes)
            range_value = bar.high - bar.low
            close_location = 0.5 if range_value <= 0 else (bar.close - bar.low) / range_value
            r20 = _return(series, index, 20)
            r60 = _return(series, index, 60)
            r120 = _return(series, index, 120)
            volatility = _volatility(series, index)
            score = (0.45 * r20 + 0.35 * r60 + 0.20 * r120) / max(volatility, 0.02)
            payload[asset] = AssetFeatures(
                return_1=_return(series, index, 1),
                return_3=_return(series, index, 3),
                return_5=_return(series, index, 5),
                return_20=r20,
                return_60=r60,
                return_120=r120,
                volatility_20=volatility,
                sma_50=_sma(series, index, 50),
                sma_80=_sma(series, index, 80),
                sma_100=_sma(series, index, 100),
                sma_120=_sma(series, index, 120),
                close=bar.close,
                close_location=max(0.0, min(1.0, close_location)),
                volume_ratio=bar.quote_volume / max(volume_median, 1e-12),
                trend_score=score,
            )
        features[day] = payload
    return features


def _selected_sma(item: AssetFeatures, length: int) -> float:
    if length == 80:
        return item.sma_80
    if length == 120:
        return item.sma_120
    raise HistoricalRotationV28Error(f"Unsupported SMA length: {length}")


def _daily_target(
    model: ModelSpec,
    signal_features: dict[str, AssetFeatures],
    prior_weights: dict[str, float],
    prior_sleeve: str,
    days_since_trend_rebalance: int,
) -> tuple[dict[str, float], str, int]:
    trend_flags = {
        asset: (
            item.close > _selected_sma(item, model.sma_length)
            and item.return_20 > 0.0
            and item.return_60 > 0.0
        )
        for asset, item in signal_features.items()
    }
    breadth = sum(trend_flags.values()) / len(ASSETS)
    btc = signal_features["BTC"]
    trend_mode = (
        btc.close > _selected_sma(btc, model.sma_length)
        and btc.return_20 > 0.0
        and btc.return_60 > 0.0
        and breadth >= model.breadth_floor
    )
    if trend_mode:
        due = prior_sleeve != "trend" or days_since_trend_rebalance >= model.rebalance_days
        if not due:
            return dict(prior_weights), "trend", days_since_trend_rebalance + 1
        ranked: list[tuple[float, str]] = []
        for asset, item in signal_features.items():
            if not trend_flags[asset] or item.return_120 <= 0.0 or item.trend_score <= 0.0:
                continue
            pullback_bonus = 1.15 if -0.06 <= item.return_3 <= -0.01 and item.return_1 > 0.0 else 1.0
            ranked.append((item.trend_score * pullback_bonus, asset))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        chosen = [asset for _, asset in ranked[: model.top_n]]
        if not chosen:
            return {}, "cash", 0
        weight = MAX_EXPOSURE / len(chosen)
        return {asset: weight for asset in chosen}, "trend", 0

    recoveries: list[tuple[float, str]] = []
    for asset, item in signal_features.items():
        if (
            item.return_5 <= model.recovery_threshold
            and item.return_1 >= 0.015
            and item.close_location >= 0.60
            and item.volume_ratio >= 1.20
            and item.return_120 > -0.35
            and btc.return_5 > -0.18
        ):
            strength = item.return_1 + abs(item.return_5) + 0.25 * max(0.0, item.volume_ratio - 1.0)
            recoveries.append((strength, asset))
    recoveries.sort(key=lambda row: (-row[0], row[1]))
    chosen_recovery = [asset for _, asset in recoveries[:2]]
    if chosen_recovery:
        weight = MAX_EXPOSURE / len(chosen_recovery)
        return {asset: weight for asset in chosen_recovery}, "recovery", 0

    positive20 = sum(item.return_20 > 0.0 for item in signal_features.values()) / len(ASSETS)
    if btc.return_20 > -0.05 and positive20 >= 1.0 / 3.0:
        neutral = [
            (item.trend_score, asset)
            for asset, item in signal_features.items()
            if (
                item.return_20 > 0.0
                and item.return_60 > 0.0
                and item.return_120 > 0.0
                and item.close > item.sma_50
                and -0.02 <= item.return_1 <= 0.03
                and item.trend_score > 0.0
            )
        ]
        neutral.sort(key=lambda row: (-row[0], row[1]))
        if neutral:
            return {neutral[0][1]: NEUTRAL_EXPOSURE}, "neutral", 0
    return {}, "cash", 0


def _maximum_drawdown(returns: list[float]) -> float:
    equity = peak = 1.0
    maximum = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        maximum = max(maximum, 1.0 - equity / peak)
    return maximum


def _compounded(returns: list[float]) -> float:
    equity = 1.0
    for value in returns:
        equity *= 1.0 + value
    return equity - 1.0


def simulate(
    model: ModelSpec,
    bars: dict[str, dict[datetime, v25.HourlyBar]],
    features: dict[datetime, dict[str, AssetFeatures]],
    start: datetime,
    end: datetime,
    cost: float,
) -> SimulationResult:
    current_weights: dict[str, float] = {}
    current_sleeve = "cash"
    days_since_trend_rebalance = 0
    daily_returns: list[float] = []
    gross_returns: list[float] = []
    turnover_total = 0.0
    action_days = 0
    selected_assets: set[str] = set()
    active_sleeves: set[str] = set()
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    sleeve_contribution: dict[str, float] = {}
    day = start
    while day <= end:
        signal_day = day - timedelta(days=1)
        next_day = day + timedelta(days=1)
        if signal_day not in features:
            raise HistoricalRotationV28Error(f"Features unavailable for {_utc(signal_day)}")
        target, sleeve, days_since_trend_rebalance = _daily_target(
            model,
            features[signal_day],
            current_weights,
            current_sleeve,
            days_since_trend_rebalance,
        )
        if sum(target.values()) > MAX_EXPOSURE + 1e-12:
            raise HistoricalRotationV28Error("Target exposure exceeds 30%")
        turnover = sum(abs(target.get(asset, 0.0) - current_weights.get(asset, 0.0)) for asset in ASSETS)
        trading_cost = 0.5 * cost * turnover
        if turnover > 1e-10 and target:
            action_days += 1
        turnover_total += turnover
        selected_assets.update(target)
        if target:
            active_sleeves.add(sleeve)
        gross = 0.0
        per_asset_gross: dict[str, float] = {}
        for asset, weight in target.items():
            entry = bars[asset][day].open
            exit_price = bars[asset][next_day].open
            value = weight * (exit_price / entry - 1.0)
            per_asset_gross[asset] = value
            gross += value
        net = gross - trading_cost
        gross_returns.append(gross)
        daily_returns.append(net)
        traded = {asset: abs(target.get(asset, 0.0) - current_weights.get(asset, 0.0)) for asset in ASSETS}
        traded_total = sum(traded.values())
        for asset in ASSETS:
            allocated_cost = trading_cost * traded[asset] / traded_total if traded_total > 0 else 0.0
            asset_contribution[asset] += per_asset_gross.get(asset, 0.0) - allocated_cost
        sleeve_contribution[sleeve] = sleeve_contribution.get(sleeve, 0.0) + net
        denominator = 1.0 + net
        if denominator <= 0.0:
            raise HistoricalRotationV28Error("Portfolio equity became nonpositive")
        drifted: dict[str, float] = {}
        for asset, weight in target.items():
            entry = bars[asset][day].open
            exit_price = bars[asset][next_day].open
            drifted[asset] = weight * (exit_price / entry) / denominator
        current_weights = drifted
        current_sleeve = sleeve
        day += timedelta(days=1)
    return SimulationResult(
        net_return=_compounded(daily_returns),
        gross_return=_compounded(gross_returns),
        maximum_drawdown=_maximum_drawdown(daily_returns),
        turnover=turnover_total,
        non_cash_action_days=action_days,
        selected_assets=sorted(selected_assets),
        active_sleeves=sorted(active_sleeves),
        daily_returns=daily_returns,
        asset_contribution={key: value for key, value in sorted(asset_contribution.items()) if abs(value) > 1e-15},
        sleeve_contribution=dict(sorted(sleeve_contribution.items())),
    )


def _discovery_blocks() -> list[VerificationWindow]:
    first = DATA_START + timedelta(days=WARMUP_DAYS)
    blocks: list[VerificationWindow] = []
    cursor = first
    number = 1
    while cursor + timedelta(days=DISCOVERY_BLOCK_DAYS - 1) <= DISCOVERY_END:
        end = cursor + timedelta(days=DISCOVERY_BLOCK_DAYS - 1)
        blocks.append(VerificationWindow(f"discovery-{number:02d}", cursor, end))
        cursor = end + timedelta(days=1)
        number += 1
    return blocks


def _model_discovery_row(
    model: ModelSpec,
    bars: dict[str, dict[datetime, v25.HourlyBar]],
    features: dict[datetime, dict[str, AssetFeatures]],
) -> dict[str, Any]:
    block_results = [simulate(model, bars, features, block.start, block.end, STRESS_COST) for block in _discovery_blocks()]
    returns = [result.net_return for result in block_results]
    daily = [value for result in block_results for value in result.daily_returns]
    return {
        "model": asdict(model),
        "model_id": model.model_id,
        "positive_blocks": sum(value > 0.0 for value in returns),
        "minimum_block_return": min(returns),
        "median_block_return": median(returns),
        "compounded_stress_return": _compounded(returns),
        "maximum_drawdown": _maximum_drawdown(daily),
        "turnover": sum(result.turnover for result in block_results),
        "block_returns": {block.name: value for block, value in zip(_discovery_blocks(), returns, strict=True)},
    }


def select_model(
    bars: dict[str, dict[datetime, v25.HourlyBar]],
    features: dict[datetime, dict[str, AssetFeatures]],
) -> tuple[ModelSpec, list[dict[str, Any]]]:
    table = [_model_discovery_row(model, bars, features) for model in MODEL_GRID]
    table.sort(
        key=lambda row: (
            -row["positive_blocks"],
            -row["minimum_block_return"],
            -row["median_block_return"],
            -row["compounded_stress_return"],
            row["maximum_drawdown"],
            row["turnover"],
            row["model_id"],
        )
    )
    chosen_id = table[0]["model_id"]
    chosen = next(model for model in MODEL_GRID if model.model_id == chosen_id)
    return chosen, table


def _benchmark(
    bars: dict[str, dict[datetime, v25.HourlyBar]],
    start: datetime,
    end: datetime,
    cost: float,
) -> tuple[float, float]:
    exit_day = end + timedelta(days=1)
    btc_raw = bars["BTC"][exit_day].open / bars["BTC"][start].open - 1.0
    equal_raw = sum(
        bars[asset][exit_day].open / bars[asset][start].open - 1.0
        for asset in ASSETS
    ) / len(ASSETS)
    return MAX_EXPOSURE * (btc_raw - cost), MAX_EXPOSURE * (equal_raw - cost)


def _combine_window_results(results: dict[str, SimulationResult]) -> dict[str, Any]:
    returns = [result.net_return for result in results.values()]
    daily = [value for result in results.values() for value in result.daily_returns]
    assets = sorted({asset for result in results.values() for asset in result.selected_assets})
    sleeves = sorted({sleeve for result in results.values() for sleeve in result.active_sleeves})
    asset_contribution: dict[str, float] = {}
    sleeve_contribution: dict[str, float] = {}
    for result in results.values():
        for key, value in result.asset_contribution.items():
            asset_contribution[key] = asset_contribution.get(key, 0.0) + value
        for key, value in result.sleeve_contribution.items():
            sleeve_contribution[key] = sleeve_contribution.get(key, 0.0) + value
    positive_assets = {key: max(0.0, value) for key, value in asset_contribution.items()}
    positive_total = sum(positive_assets.values())
    positive_windows = {name: max(0.0, result.net_return) for name, result in results.items()}
    positive_window_total = sum(positive_windows.values())
    return {
        "net_compounded_return": _compounded(returns),
        "maximum_drawdown": _maximum_drawdown(daily),
        "turnover": sum(result.turnover for result in results.values()),
        "non_cash_action_days": sum(result.non_cash_action_days for result in results.values()),
        "selected_assets": assets,
        "active_sleeves": sleeves,
        "window_returns": {name: result.net_return for name, result in results.items()},
        "window_action_days": {name: result.non_cash_action_days for name, result in results.items()},
        "asset_net_contribution": dict(sorted(asset_contribution.items())),
        "sleeve_net_contribution": dict(sorted(sleeve_contribution.items())),
        "maximum_positive_asset_share": 0.0 if positive_total <= 0 else max(positive_assets.values(), default=0.0) / positive_total,
        "maximum_positive_window_share": 0.0 if positive_window_total <= 0 else max(positive_windows.values(), default=0.0) / positive_window_total,
    }


def run_rotation(max_workers: int = 16) -> dict[str, Any]:
    if not PROTOCOL_PATH.is_file():
        raise HistoricalRotationV28Error(f"Protocol missing: {PROTOCOL_PATH}")
    if len(MODEL_GRID) != 32:
        raise HistoricalRotationV28Error("Frozen model grid must contain exactly 32 models")
    downloaded, inventory = download_inputs(max_workers=max_workers)
    bars, dates = assemble_bars(downloaded)
    features = build_features(bars, dates)
    chosen, selection_table = select_model(bars, features)

    discovery_standard_results = {
        block.name: simulate(chosen, bars, features, block.start, block.end, STANDARD_COST)
        for block in _discovery_blocks()
    }
    discovery_stress_results = {
        block.name: simulate(chosen, bars, features, block.start, block.end, STRESS_COST)
        for block in _discovery_blocks()
    }
    discovery_standard = _combine_window_results(discovery_standard_results)
    discovery_stress = _combine_window_results(discovery_stress_results)
    discovery_gates = {
        "six_positive_blocks": sum(value > 0.0 for value in discovery_stress["window_returns"].values()) >= 6,
        "positive_median_block": median(discovery_stress["window_returns"].values()) > 0.0,
        "positive_standard_return": discovery_standard["net_compounded_return"] > 0.0,
        "positive_stress_return": discovery_stress["net_compounded_return"] > 0.0,
        "minimum_block_above_minus_four_percent": min(discovery_stress["window_returns"].values()) > -0.04,
        "drawdown_cap": discovery_stress["maximum_drawdown"] <= 0.10,
    }

    validation_standard_results = {
        window.name: simulate(chosen, bars, features, window.start, window.end, STANDARD_COST)
        for window in VALIDATION_WINDOWS
    }
    validation_stress_results = {
        window.name: simulate(chosen, bars, features, window.start, window.end, STRESS_COST)
        for window in VALIDATION_WINDOWS
    }
    validation_standard = _combine_window_results(validation_standard_results)
    validation_stress = _combine_window_results(validation_stress_results)
    btc_benchmark, equal_benchmark = _benchmark(bars, VALIDATION_START, VALIDATION_END, STANDARD_COST)
    gates = {
        "discovery_robustness": all(discovery_gates.values()),
        "positive_standard_return": validation_standard["net_compounded_return"] > 0.0,
        "positive_stress_return": validation_stress["net_compounded_return"] > 0.0,
        "five_positive_standard_quarters": all(value > 0.0 for value in validation_standard["window_returns"].values()),
        "five_positive_stress_quarters": all(value > 0.0 for value in validation_stress["window_returns"].values()),
        "four_actions_each_quarter": all(value >= 4 for value in validation_standard["window_action_days"].values()),
        "thirty_actions": validation_standard["non_cash_action_days"] >= 30,
        "four_assets": len(validation_standard["selected_assets"]) >= 4,
        "two_sleeves": len(validation_standard["active_sleeves"]) >= 2,
        "drawdown_cap": validation_standard["maximum_drawdown"] <= 0.08,
        "beat_cash": validation_standard["net_compounded_return"] > 0.0,
        "beat_btc_benchmark": validation_standard["net_compounded_return"] > btc_benchmark,
        "beat_equal_weight_benchmark": validation_standard["net_compounded_return"] > equal_benchmark,
        "asset_concentration": validation_standard["maximum_positive_asset_share"] <= 0.55,
        "quarter_concentration": validation_standard["maximum_positive_window_share"] <= 0.40,
        "all_bars_complete": True,
    }
    source_path = Path(__file__).resolve()
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "changes_track_a": False,
        "cannot_replace_forward_evidence": True,
        "assets": list(ASSETS),
        "data_start_utc": _utc(DATA_START),
        "discovery_end_utc": _utc(DISCOVERY_END),
        "validation_start_utc": _utc(VALIDATION_START),
        "validation_end_utc": _utc(VALIDATION_END),
        "model_grid_size": len(MODEL_GRID),
        "chosen_model": asdict(chosen) | {"model_id": chosen.model_id},
        "selection_table": selection_table,
        "discovery_blocks": [asdict(block) | {"start": _utc(block.start), "end": _utc(block.end)} for block in _discovery_blocks()],
        "verification_windows": [asdict(window) | {"start": _utc(window.start), "end": _utc(window.end)} for window in VALIDATION_WINDOWS],
        "discovery": {
            "standard": discovery_standard,
            "stress": discovery_stress,
            "gates": discovery_gates,
        },
        "verification": {
            "standard": validation_standard,
            "stress": validation_stress,
            "btc_30pct_benchmark_return": btc_benchmark,
            "equal_weight_30pct_benchmark_return": equal_benchmark,
            "gates": gates,
        },
        "source_inventory": inventory,
        "source_inventory_sha256": hashlib.sha256(canonical_json(inventory).encode("utf-8")).hexdigest(),
        "fingerprints": {
            "protocol_sha256": hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest(),
            "implementation_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "chosen_model_sha256": hashlib.sha256(canonical_json(asdict(chosen)).encode("utf-8")).hexdigest(),
        },
        "screening_status": "FIVE_QUARTER_ROTATION_BREAKTHROUGH_CANDIDATE" if all(gates.values()) else "NOT_FIVE_QUARTER_VERIFIED",
    }
    report["report_sha256"] = hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
    return report


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run isolated v2.8 regime-adaptive rotation research.")
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--max-workers", type=int, default=16)
    args = parser.parse_args(argv)
    report = run_rotation(max_workers=args.max_workers)
    _atomic_json(Path(args.json_out), report)
    verification = report["verification"]
    print(json.dumps({
        "status": report["screening_status"],
        "chosen_model": report["chosen_model"],
        "standard_return": verification["standard"]["net_compounded_return"],
        "stress_return": verification["stress"]["net_compounded_return"],
        "standard_quarters": verification["standard"]["window_returns"],
        "stress_quarters": verification["stress"]["window_returns"],
        "action_days": verification["standard"]["window_action_days"],
        "maximum_drawdown": verification["standard"]["maximum_drawdown"],
        "report_sha256": report["report_sha256"],
        "authorizes_trading": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
