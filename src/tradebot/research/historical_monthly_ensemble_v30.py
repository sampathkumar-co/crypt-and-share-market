from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median, pstdev
from typing import Any

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import historical_proxy_screen_v25 as v25

MODE = "HISTORICAL_MONTHLY_TREND_RECOVERY_ENSEMBLE_ONLY"
SCHEMA_VERSION = "3.0-monthly-trend-recovery-ensemble"
PROTOCOL_PATH = Path("research/V30_MONTHLY_TREND_RECOVERY_ENSEMBLE_PROTOCOL.md")
ASSETS = ("BTC", "ETH", "SOL", "AVAX", "LINK", "DOGE")
SYMBOLS = {asset: f"{asset}USDT" for asset in ASSETS}
BASE_URL = "https://data.binance.vision/data"
DATA_START = datetime(2021, 1, 1, tzinfo=timezone.utc)
DISCOVERY_END = datetime(2026, 1, 31, tzinfo=timezone.utc)
VERIFICATION_START = datetime(2026, 2, 1, tzinfo=timezone.utc)
VERIFICATION_END = datetime(2026, 6, 30, tzinfo=timezone.utc)
EXIT_DATE = datetime(2026, 7, 1, tzinfo=timezone.utc)
WARMUP_DAYS = 200
STANDARD_COST = 0.002
STRESS_COST = 0.004
VOLATILITY_TARGET = 0.025
DEFENSIVE_BTC_EXPOSURE = 0.05
MONTHLY_LOSS_BRAKE = -0.015


class HistoricalMonthlyEnsembleV30Error(RuntimeError):
    """Raised when v3.0 cannot be reproduced safely."""


@dataclass(frozen=True)
class ModelSpec:
    trend_sma: int
    rebalance_days: int
    top_n: int
    maximum_exposure: float
    recovery_threshold: float
    recovery_holding_days: int

    @property
    def model_id(self) -> str:
        return (
            f"sma{self.trend_sma}-rebalance{self.rebalance_days}"
            f"-top{self.top_n}-exposure{int(round(self.maximum_exposure * 100))}"
            f"-recovery{int(round(abs(self.recovery_threshold) * 100))}"
            f"-hold{self.recovery_holding_days}"
        )


MODEL_GRID = tuple(
    ModelSpec(sma, cadence, top_n, exposure, recovery, holding)
    for sma in (50, 100)
    for cadence in (5, 10)
    for top_n in (1, 2)
    for exposure in (0.10, 0.20)
    for recovery in (-0.08, -0.12)
    for holding in (2, 4)
)


@dataclass(frozen=True)
class MonthWindow:
    name: str
    start: datetime
    end: datetime


VERIFICATION_WINDOWS = (
    MonthWindow("2026-02", datetime(2026, 2, 1, tzinfo=timezone.utc), datetime(2026, 2, 28, tzinfo=timezone.utc)),
    MonthWindow("2026-03", datetime(2026, 3, 1, tzinfo=timezone.utc), datetime(2026, 3, 31, tzinfo=timezone.utc)),
    MonthWindow("2026-04", datetime(2026, 4, 1, tzinfo=timezone.utc), datetime(2026, 4, 30, tzinfo=timezone.utc)),
    MonthWindow("2026-05", datetime(2026, 5, 1, tzinfo=timezone.utc), datetime(2026, 5, 31, tzinfo=timezone.utc)),
    MonthWindow("2026-06", datetime(2026, 6, 1, tzinfo=timezone.utc), datetime(2026, 6, 30, tzinfo=timezone.utc)),
)


@dataclass(frozen=True)
class Features:
    return_1: float
    return_3: float
    return_5: float
    return_10: float
    return_20: float
    return_60: float
    return_120: float
    return_180: float
    volatility_20: float
    sma_20: float
    sma_50: float
    sma_100: float
    sma_200: float
    close: float
    close_location: float
    volume_ratio: float
    drawdown_20: float
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
    brake_triggered: bool


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _months(start: datetime, end: datetime) -> list[str]:
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
        for month in _months(DATA_START, VERIFICATION_END):
            requests[f"monthly:{asset}:{month}"] = (
                f"{BASE_URL}/spot/monthly/klines/{symbol}/1d/{symbol}-1d-{month}.zip"
            )
        requests[f"exit:{asset}:2026-07-01"] = (
            f"{BASE_URL}/spot/daily/klines/{symbol}/1d/{symbol}-1d-2026-07-01.zip"
        )
    return requests


def download_inputs(max_workers: int = 20) -> tuple[dict[str, v25.DownloadedArchive], list[dict[str, str]]]:
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
        raise HistoricalMonthlyEnsembleV30Error(
            f"{len(failures)} required archives failed: {sample}"
        )
    inventory = [
        {"key": key, "url": archive.url, "sha256": archive.sha256}
        for key, archive in sorted(downloaded.items())
    ]
    return downloaded, inventory


def assemble_bars(
    downloaded: dict[str, v25.DownloadedArchive],
) -> tuple[dict[str, dict[datetime, v25.HourlyBar]], list[datetime]]:
    bars: dict[str, dict[datetime, v25.HourlyBar]] = {asset: {} for asset in ASSETS}
    for key, archive in sorted(downloaded.items()):
        asset = key.split(":")[1]
        for timestamp, bar in v25._parse_klines(archive).items():
            day = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
            prior = bars[asset].get(day)
            if prior is not None and prior != bar:
                raise HistoricalMonthlyEnsembleV30Error(
                    f"Conflicting daily bar: {asset} {_utc(day)}"
                )
            bars[asset][day] = bar
    dates: list[datetime] = []
    day = DATA_START
    while day <= EXIT_DATE:
        dates.append(day)
        day += timedelta(days=1)
    for asset in ASSETS:
        missing = [day for day in dates if day not in bars[asset]]
        if missing:
            raise HistoricalMonthlyEnsembleV30Error(
                f"{asset} missing {len(missing)} required bars; first={_utc(missing[0])}"
            )
    return bars, dates


def _return(closes: list[float], index: int, lag: int) -> float:
    return closes[index] / closes[index - lag] - 1.0


def _sma(closes: list[float], index: int, length: int) -> float:
    values = closes[index - length + 1 : index + 1]
    if len(values) != length:
        raise HistoricalMonthlyEnsembleV30Error(
            f"SMA{length} unavailable at index {index}"
        )
    return sum(values) / length


def _volatility(closes: list[float], index: int, length: int = 20) -> float:
    values = closes[index - length : index + 1]
    returns = [values[offset] / values[offset - 1] - 1.0 for offset in range(1, len(values))]
    return 0.0 if len(returns) < 2 else pstdev(returns)


def build_features(
    bars: dict[str, dict[datetime, v25.HourlyBar]],
    dates: list[datetime],
) -> dict[datetime, dict[str, Features]]:
    closes = {asset: [bars[asset][day].close for day in dates] for asset in ASSETS}
    payload: dict[datetime, dict[str, Features]] = {}
    for index in range(200, len(dates)):
        day = dates[index]
        daily: dict[str, Features] = {}
        for asset in ASSETS:
            series = closes[asset]
            bar = bars[asset][day]
            prior_volumes = [
                bars[asset][dates[offset]].quote_volume
                for offset in range(index - 20, index)
            ]
            r20 = _return(series, index, 20)
            r60 = _return(series, index, 60)
            r120 = _return(series, index, 120)
            volatility = _volatility(series, index)
            high20 = max(series[index - 19 : index + 1])
            daily_range = bar.high - bar.low
            daily[asset] = Features(
                return_1=_return(series, index, 1),
                return_3=_return(series, index, 3),
                return_5=_return(series, index, 5),
                return_10=_return(series, index, 10),
                return_20=r20,
                return_60=r60,
                return_120=r120,
                return_180=_return(series, index, 180),
                volatility_20=volatility,
                sma_20=_sma(series, index, 20),
                sma_50=_sma(series, index, 50),
                sma_100=_sma(series, index, 100),
                sma_200=_sma(series, index, 200),
                close=bar.close,
                close_location=(
                    0.5 if daily_range <= 0.0 else max(0.0, min(1.0, (bar.close - bar.low) / daily_range))
                ),
                volume_ratio=bar.quote_volume / max(median(prior_volumes), 1e-12),
                drawdown_20=series[index] / high20 - 1.0,
                trend_score=(
                    0.50 * r20 + 0.30 * r60 + 0.20 * r120
                ) / max(volatility, 0.02),
            )
        payload[day] = daily
    return payload


def _trend_sma(item: Features, length: int) -> float:
    return item.sma_50 if length == 50 else item.sma_100


def _target(
    model: ModelSpec,
    features: dict[str, Features],
    previous_assets: tuple[str, ...],
    previous_sleeve: str,
    trend_age: int,
    recovery_days_left: int,
    brake_active: bool,
) -> tuple[dict[str, float], tuple[str, ...], str, int, int]:
    if brake_active:
        return {}, (), "cash", 0, 0
    if previous_sleeve == "recovery" and recovery_days_left > 0:
        weight = model.maximum_exposure / len(previous_assets)
        return (
            {asset: weight for asset in previous_assets},
            previous_assets,
            "recovery",
            0,
            recovery_days_left - 1,
        )

    btc = features["BTC"]
    trend_flags = {
        asset: (
            item.close > _trend_sma(item, model.trend_sma)
            and item.return_20 > 0.0
            and item.return_60 > 0.0
        )
        for asset, item in features.items()
    }
    trend_mode = (
        btc.close > _trend_sma(btc, model.trend_sma)
        and btc.return_20 > 0.0
        and btc.return_60 > 0.0
        and sum(trend_flags.values()) / len(ASSETS) >= 1.0 / 3.0
    )
    if trend_mode:
        due = previous_sleeve != "trend" or trend_age >= model.rebalance_days - 1
        selected = previous_assets
        next_age = trend_age + 1
        if due:
            ranked: list[tuple[float, str]] = []
            for asset, item in features.items():
                if (
                    not trend_flags[asset]
                    or item.return_120 <= 0.0
                    or item.return_180 <= 0.0
                    or item.trend_score <= 0.0
                    or item.return_1 > 0.08
                    or item.return_5 > 0.20
                ):
                    continue
                bonus = 1.15 if -0.08 <= item.return_3 <= -0.01 and item.return_1 > 0.0 else 1.0
                ranked.append((item.trend_score * bonus, asset))
            ranked.sort(key=lambda row: (-row[0], row[1]))
            selected = tuple(asset for _, asset in ranked[: model.top_n])
            next_age = 0
        if selected:
            selected_vol = median([features[asset].volatility_20 for asset in selected])
            exposure = model.maximum_exposure * min(
                1.0, VOLATILITY_TARGET / max(selected_vol, 1e-12)
            )
            if exposure > 0.0:
                weight = exposure / len(selected)
                return (
                    {asset: weight for asset in selected},
                    selected,
                    "trend",
                    next_age,
                    0,
                )

    recoveries: list[tuple[float, str]] = []
    for asset, item in features.items():
        if (
            item.return_5 <= model.recovery_threshold
            and item.drawdown_20 <= 1.25 * model.recovery_threshold
            and 0.01 <= item.return_1 <= 0.08
            and item.close_location >= 0.60
            and item.volume_ratio >= 1.20
            and item.close >= 0.60 * item.sma_200
            and btc.return_5 > -0.20
        ):
            score = (
                item.return_1
                + abs(item.return_5)
                + 0.20 * max(0.0, item.volume_ratio - 1.0)
            )
            recoveries.append((score, asset))
    recoveries.sort(key=lambda row: (-row[0], row[1]))
    selected_recovery = tuple(asset for _, asset in recoveries[:2])
    if selected_recovery:
        weight = model.maximum_exposure / len(selected_recovery)
        return (
            {asset: weight for asset in selected_recovery},
            selected_recovery,
            "recovery",
            0,
            model.recovery_holding_days - 1,
        )

    defensive = (
        btc.close > btc.sma_200
        and btc.return_20 > 0.0
        and -0.04 <= btc.return_1 <= 0.04
    )
    if defensive:
        exposure = min(DEFENSIVE_BTC_EXPOSURE, model.maximum_exposure)
        return {"BTC": exposure}, ("BTC",), "defensive_btc", 0, 0
    return {}, (), "cash", 0, 0


def _compounded(values: list[float]) -> float:
    equity = 1.0
    for value in values:
        equity *= 1.0 + value
    return equity - 1.0


def _maximum_drawdown(values: list[float]) -> float:
    equity = peak = 1.0
    maximum = 0.0
    for value in values:
        equity *= 1.0 + value
        peak = max(peak, equity)
        maximum = max(maximum, 1.0 - equity / peak)
    return maximum


def simulate(
    model: ModelSpec,
    bars: dict[str, dict[datetime, v25.HourlyBar]],
    features: dict[datetime, dict[str, Features]],
    start: datetime,
    end: datetime,
    cost: float,
) -> SimulationResult:
    current_weights: dict[str, float] = {}
    selected_assets: tuple[str, ...] = ()
    sleeve = "cash"
    trend_age = 0
    recovery_days_left = 0
    brake_active = False
    daily_returns: list[float] = []
    gross_returns: list[float] = []
    turnover_total = 0.0
    action_days = 0
    used_assets: set[str] = set()
    used_sleeves: set[str] = set()
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    sleeve_contribution: dict[str, float] = {}
    day = start
    while day <= end:
        if not brake_active and _compounded(daily_returns) <= MONTHLY_LOSS_BRAKE:
            brake_active = True
        signal_day = day - timedelta(days=1)
        next_day = day + timedelta(days=1)
        if signal_day not in features:
            raise HistoricalMonthlyEnsembleV30Error(
                f"Features unavailable for {_utc(signal_day)}"
            )
        target, selected_assets, next_sleeve, trend_age, recovery_days_left = _target(
            model,
            features[signal_day],
            selected_assets,
            sleeve,
            trend_age,
            recovery_days_left,
            brake_active,
        )
        exposure = sum(target.values())
        if exposure > model.maximum_exposure + 1e-12 or exposure > 0.20 + 1e-12:
            raise HistoricalMonthlyEnsembleV30Error("Exposure cap violated")
        turnover_by_asset = {
            asset: abs(target.get(asset, 0.0) - current_weights.get(asset, 0.0))
            for asset in ASSETS
        }
        turnover = sum(turnover_by_asset.values())
        trading_cost = 0.5 * cost * turnover
        if turnover > 1e-10 and target:
            action_days += 1
        turnover_total += turnover
        used_assets.update(target)
        if target:
            used_sleeves.add(next_sleeve)
        gross = 0.0
        per_asset: dict[str, float] = {}
        for asset, weight in target.items():
            raw = bars[asset][next_day].open / bars[asset][day].open - 1.0
            contribution = weight * raw
            per_asset[asset] = contribution
            gross += contribution
        net = gross - trading_cost
        gross_returns.append(gross)
        daily_returns.append(net)
        traded_total = sum(turnover_by_asset.values())
        for asset in ASSETS:
            allocated_cost = (
                trading_cost * turnover_by_asset[asset] / traded_total
                if traded_total > 0.0
                else 0.0
            )
            asset_contribution[asset] += per_asset.get(asset, 0.0) - allocated_cost
        sleeve_contribution[next_sleeve] = sleeve_contribution.get(next_sleeve, 0.0) + net
        denominator = 1.0 + net
        if denominator <= 0.0:
            raise HistoricalMonthlyEnsembleV30Error("Portfolio equity became nonpositive")
        drifted = {
            asset: weight
            * (bars[asset][next_day].open / bars[asset][day].open)
            / denominator
            for asset, weight in target.items()
        }
        drifted_exposure = sum(drifted.values())
        if drifted_exposure > model.maximum_exposure and drifted_exposure > 0.0:
            scale = model.maximum_exposure / drifted_exposure
            drifted = {asset: weight * scale for asset, weight in drifted.items()}
        current_weights = drifted
        sleeve = next_sleeve
        day += timedelta(days=1)

    final_turnover = sum(current_weights.values())
    if final_turnover > 1e-12:
        final_cost = 0.5 * cost * final_turnover
        daily_returns.append(-final_cost)
        gross_returns.append(0.0)
        turnover_total += final_turnover
        for asset, weight in current_weights.items():
            asset_contribution[asset] -= final_cost * weight / final_turnover
        sleeve_contribution["final_liquidation"] = -final_cost

    return SimulationResult(
        net_return=_compounded(daily_returns),
        gross_return=_compounded(gross_returns),
        maximum_drawdown=_maximum_drawdown(daily_returns),
        turnover=turnover_total,
        non_cash_action_days=action_days,
        selected_assets=sorted(used_assets),
        active_sleeves=sorted(used_sleeves),
        daily_returns=daily_returns,
        asset_contribution={
            key: value
            for key, value in sorted(asset_contribution.items())
            if abs(value) > 1e-15
        },
        sleeve_contribution=dict(sorted(sleeve_contribution.items())),
        brake_triggered=brake_active,
    )


def _month_windows(start: datetime, end: datetime) -> list[MonthWindow]:
    current = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    windows: list[MonthWindow] = []
    while current <= end:
        next_month = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end = next_month - timedelta(days=1)
        if current >= start and month_end <= end:
            windows.append(MonthWindow(current.strftime("%Y-%m"), current, month_end))
        current = next_month
    return windows


def discovery_windows() -> list[MonthWindow]:
    warmup_end = DATA_START + timedelta(days=WARMUP_DAYS - 1)
    first_month = (warmup_end.replace(day=28) + timedelta(days=4)).replace(day=1)
    return _month_windows(first_month, DISCOVERY_END)


def _combine(results: dict[str, SimulationResult]) -> dict[str, Any]:
    window_returns = {name: result.net_return for name, result in results.items()}
    daily = [value for result in results.values() for value in result.daily_returns]
    assets = sorted({asset for result in results.values() for asset in result.selected_assets})
    sleeves = sorted({item for result in results.values() for item in result.active_sleeves})
    asset_contribution: dict[str, float] = {}
    sleeve_contribution: dict[str, float] = {}
    for result in results.values():
        for key, value in result.asset_contribution.items():
            asset_contribution[key] = asset_contribution.get(key, 0.0) + value
        for key, value in result.sleeve_contribution.items():
            sleeve_contribution[key] = sleeve_contribution.get(key, 0.0) + value
    positive_assets = {key: max(0.0, value) for key, value in asset_contribution.items()}
    positive_windows = {key: max(0.0, value) for key, value in window_returns.items()}
    asset_total = sum(positive_assets.values())
    window_total = sum(positive_windows.values())
    return {
        "net_compounded_return": _compounded(list(window_returns.values())),
        "maximum_drawdown": _maximum_drawdown(daily),
        "turnover": sum(result.turnover for result in results.values()),
        "non_cash_action_days": sum(result.non_cash_action_days for result in results.values()),
        "selected_assets": assets,
        "active_sleeves": sleeves,
        "window_returns": window_returns,
        "window_action_days": {
            name: result.non_cash_action_days for name, result in results.items()
        },
        "brake_triggered_windows": [
            name for name, result in results.items() if result.brake_triggered
        ],
        "asset_net_contribution": dict(sorted(asset_contribution.items())),
        "sleeve_net_contribution": dict(sorted(sleeve_contribution.items())),
        "maximum_positive_asset_share": (
            0.0 if asset_total <= 0.0 else max(positive_assets.values(), default=0.0) / asset_total
        ),
        "maximum_positive_window_share": (
            0.0 if window_total <= 0.0 else max(positive_windows.values(), default=0.0) / window_total
        ),
    }


def _discovery_row(
    model: ModelSpec,
    bars: dict[str, dict[datetime, v25.HourlyBar]],
    features: dict[datetime, dict[str, Features]],
) -> dict[str, Any]:
    results = {
        window.name: simulate(model, bars, features, window.start, window.end, STRESS_COST)
        for window in discovery_windows()
    }
    combined = _combine(results)
    returns = list(combined["window_returns"].values())
    return {
        "model": asdict(model),
        "model_id": model.model_id,
        "positive_months": sum(value > 0.0 for value in returns),
        "minimum_month_return": min(returns),
        "median_month_return": median(returns),
        "compounded_stress_return": combined["net_compounded_return"],
        "maximum_drawdown": combined["maximum_drawdown"],
        "turnover": combined["turnover"],
        "month_returns": combined["window_returns"],
    }


def select_model(
    bars: dict[str, dict[datetime, v25.HourlyBar]],
    features: dict[datetime, dict[str, Features]],
) -> tuple[ModelSpec, list[dict[str, Any]]]:
    table = [_discovery_row(model, bars, features) for model in MODEL_GRID]
    table.sort(
        key=lambda row: (
            -row["positive_months"],
            -row["minimum_month_return"],
            -row["median_month_return"],
            -row["compounded_stress_return"],
            row["maximum_drawdown"],
            row["turnover"],
            row["model_id"],
        )
    )
    chosen = next(model for model in MODEL_GRID if model.model_id == table[0]["model_id"])
    return chosen, table


def _benchmarks(
    model: ModelSpec,
    bars: dict[str, dict[datetime, v25.HourlyBar]],
    cost: float,
) -> tuple[dict[str, float], dict[str, float]]:
    btc: dict[str, float] = {}
    equal: dict[str, float] = {}
    for window in VERIFICATION_WINDOWS:
        exit_day = window.end + timedelta(days=1)
        btc_raw = bars["BTC"][exit_day].open / bars["BTC"][window.start].open - 1.0
        equal_raw = sum(
            bars[asset][exit_day].open / bars[asset][window.start].open - 1.0
            for asset in ASSETS
        ) / len(ASSETS)
        btc[window.name] = model.maximum_exposure * (btc_raw - cost)
        equal[window.name] = model.maximum_exposure * (equal_raw - cost)
    return btc, equal


def run_ensemble(max_workers: int = 20) -> dict[str, Any]:
    if not PROTOCOL_PATH.is_file():
        raise HistoricalMonthlyEnsembleV30Error(f"Protocol missing: {PROTOCOL_PATH}")
    if len(MODEL_GRID) != 64:
        raise HistoricalMonthlyEnsembleV30Error("Frozen model grid must contain exactly 64 models")
    downloaded, inventory = download_inputs(max_workers=max_workers)
    bars, dates = assemble_bars(downloaded)
    features = build_features(bars, dates)
    discovery = discovery_windows()
    if len(discovery) != 54:
        raise HistoricalMonthlyEnsembleV30Error(
            f"Expected 54 discovery months, found {len(discovery)}"
        )
    chosen, selection_table = select_model(bars, features)

    discovery_standard_results = {
        window.name: simulate(chosen, bars, features, window.start, window.end, STANDARD_COST)
        for window in discovery
    }
    discovery_stress_results = {
        window.name: simulate(chosen, bars, features, window.start, window.end, STRESS_COST)
        for window in discovery
    }
    discovery_standard = _combine(discovery_standard_results)
    discovery_stress = _combine(discovery_stress_results)
    discovery_gates = {
        "thirty_two_positive_months": sum(
            value > 0.0 for value in discovery_stress["window_returns"].values()
        ) >= 32,
        "positive_median_month": median(discovery_stress["window_returns"].values()) > 0.0,
        "positive_standard_return": discovery_standard["net_compounded_return"] > 0.0,
        "positive_stress_return": discovery_stress["net_compounded_return"] > 0.0,
        "minimum_month_above_minus_three_percent": min(
            discovery_stress["window_returns"].values()
        ) > -0.03,
        "drawdown_cap": discovery_stress["maximum_drawdown"] <= 0.08,
    }

    verification_standard_results = {
        window.name: simulate(chosen, bars, features, window.start, window.end, STANDARD_COST)
        for window in VERIFICATION_WINDOWS
    }
    verification_stress_results = {
        window.name: simulate(chosen, bars, features, window.start, window.end, STRESS_COST)
        for window in VERIFICATION_WINDOWS
    }
    verification_standard = _combine(verification_standard_results)
    verification_stress = _combine(verification_stress_results)
    btc_windows, equal_windows = _benchmarks(chosen, bars, STANDARD_COST)
    btc_benchmark = _compounded(list(btc_windows.values()))
    equal_benchmark = _compounded(list(equal_windows.values()))
    gates = {
        "discovery_robustness": all(discovery_gates.values()),
        "positive_standard_return": verification_standard["net_compounded_return"] > 0.0,
        "positive_stress_return": verification_stress["net_compounded_return"] > 0.0,
        "five_positive_standard_months": all(
            value > 0.0 for value in verification_standard["window_returns"].values()
        ),
        "five_positive_stress_months": all(
            value > 0.0 for value in verification_stress["window_returns"].values()
        ),
        "two_actions_each_month": all(
            value >= 2 for value in verification_standard["window_action_days"].values()
        ),
        "fifteen_actions": verification_standard["non_cash_action_days"] >= 15,
        "four_assets": len(verification_standard["selected_assets"]) >= 4,
        "trend_and_recovery": {
            "trend", "recovery"
        }.issubset(set(verification_standard["active_sleeves"])),
        "drawdown_cap": verification_standard["maximum_drawdown"] <= 0.05,
        "beat_cash": verification_standard["net_compounded_return"] > 0.0,
        "beat_btc_benchmark": verification_standard["net_compounded_return"] > btc_benchmark,
        "beat_equal_weight_benchmark": verification_standard["net_compounded_return"] > equal_benchmark,
        "asset_concentration": verification_standard["maximum_positive_asset_share"] <= 0.55,
        "month_concentration": verification_standard["maximum_positive_window_share"] <= 0.30,
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
        "verification_start_utc": _utc(VERIFICATION_START),
        "verification_end_utc": _utc(VERIFICATION_END),
        "model_grid_size": len(MODEL_GRID),
        "chosen_model": asdict(chosen) | {"model_id": chosen.model_id},
        "selection_table": selection_table,
        "discovery_windows": [
            {"name": item.name, "start": _utc(item.start), "end": _utc(item.end)}
            for item in discovery
        ],
        "verification_windows": [
            {"name": item.name, "start": _utc(item.start), "end": _utc(item.end)}
            for item in VERIFICATION_WINDOWS
        ],
        "discovery": {
            "standard": discovery_standard,
            "stress": discovery_stress,
            "gates": discovery_gates,
        },
        "verification": {
            "standard": verification_standard,
            "stress": verification_stress,
            "btc_benchmark_window_returns": btc_windows,
            "equal_weight_benchmark_window_returns": equal_windows,
            "btc_max_exposure_benchmark_return": btc_benchmark,
            "equal_weight_max_exposure_benchmark_return": equal_benchmark,
            "gates": gates,
        },
        "source_inventory": inventory,
        "source_inventory_sha256": hashlib.sha256(
            canonical_json(inventory).encode("utf-8")
        ).hexdigest(),
        "fingerprints": {
            "protocol_sha256": hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest(),
            "implementation_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "chosen_model_sha256": hashlib.sha256(
                canonical_json(asdict(chosen)).encode("utf-8")
            ).hexdigest(),
        },
        "screening_status": (
            "VERIFIED_FIVE_MONTH_ENSEMBLE_BREAKTHROUGH"
            if all(gates.values())
            else "NOT_VERIFIED_FIVE_MONTH_ENSEMBLE_BREAKTHROUGH"
        ),
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run isolated v3.0 monthly trend-recovery ensemble research."
    )
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--max-workers", type=int, default=20)
    args = parser.parse_args(argv)
    report = run_ensemble(max_workers=args.max_workers)
    _atomic_json(Path(args.json_out), report)
    verification = report["verification"]
    print(
        json.dumps(
            {
                "status": report["screening_status"],
                "chosen_model": report["chosen_model"],
                "standard_return": verification["standard"]["net_compounded_return"],
                "stress_return": verification["stress"]["net_compounded_return"],
                "standard_months": verification["standard"]["window_returns"],
                "stress_months": verification["stress"]["window_returns"],
                "action_days": verification["standard"]["window_action_days"],
                "maximum_drawdown": verification["standard"]["maximum_drawdown"],
                "report_sha256": report["report_sha256"],
                "authorizes_trading": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
