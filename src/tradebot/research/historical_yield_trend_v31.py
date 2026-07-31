from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median, pstdev
from typing import Any
from urllib.request import Request, urlopen

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import historical_proxy_screen_v25 as v25

MODE = "HISTORICAL_YIELD_TREND_OVERLAY_ONLY"
SCHEMA_VERSION = "3.1-yield-trend-overlay"
PROTOCOL_PATH = Path("research/V31_YIELD_TREND_OVERLAY_PROTOCOL.md")
ADDENDUM_PATH = Path("research/V31_SOURCE_AVAILABILITY_ADDENDUM.md")
ASSETS = ("BTC", "ETH")
SYMBOLS = {asset: f"{asset}USDT" for asset in ASSETS}
BASE_URL = "https://data.binance.vision/data"
FRED_URL = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv"
    "?id=DGS3MO&cosd=2017-08-31&coed=2025-12-31"
)
DATA_START = datetime(2017, 9, 1, tzinfo=timezone.utc)
DISCOVERY_START = datetime(2018, 7, 1, tzinfo=timezone.utc)
DISCOVERY_END = datetime(2020, 12, 31, tzinfo=timezone.utc)
VERIFICATION_START = datetime(2021, 1, 1, tzinfo=timezone.utc)
VERIFICATION_END = datetime(2025, 12, 31, tzinfo=timezone.utc)
EXIT_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
WARMUP_DAYS = 200
STANDARD_COST = 0.002
STRESS_COST = 0.004


class HistoricalYieldTrendV31Error(RuntimeError):
    """Raised when v3.1 cannot be reproduced safely."""


@dataclass(frozen=True)
class ModelSpec:
    sma_length: int
    rebalance_days: int
    top_n: int
    maximum_exposure: float
    volatility_target: float
    drawdown_brake: float

    @property
    def model_id(self) -> str:
        return (
            f"sma{self.sma_length}-rebalance{self.rebalance_days}"
            f"-top{self.top_n}-exposure{int(round(self.maximum_exposure * 100))}"
            f"-vol{int(round(self.volatility_target * 100))}"
            f"-brake{int(round(self.drawdown_brake * 100))}"
        )


MODEL_GRID = tuple(
    ModelSpec(sma, cadence, top_n, exposure, volatility, brake)
    for sma in (100, 200)
    for cadence in (5, 10)
    for top_n in (1, 2)
    for exposure in (0.10, 0.20)
    for volatility in (0.02, 0.03)
    for brake in (0.10, 0.20)
)


@dataclass(frozen=True)
class Period:
    name: str
    start: datetime
    end: datetime


DISCOVERY_PERIODS = (
    Period("2018-Q3", datetime(2018, 7, 1, tzinfo=timezone.utc), datetime(2018, 9, 30, tzinfo=timezone.utc)),
    Period("2018-Q4", datetime(2018, 10, 1, tzinfo=timezone.utc), datetime(2018, 12, 31, tzinfo=timezone.utc)),
    Period("2019-Q1", datetime(2019, 1, 1, tzinfo=timezone.utc), datetime(2019, 3, 31, tzinfo=timezone.utc)),
    Period("2019-Q2", datetime(2019, 4, 1, tzinfo=timezone.utc), datetime(2019, 6, 30, tzinfo=timezone.utc)),
    Period("2019-Q3", datetime(2019, 7, 1, tzinfo=timezone.utc), datetime(2019, 9, 30, tzinfo=timezone.utc)),
    Period("2019-Q4", datetime(2019, 10, 1, tzinfo=timezone.utc), datetime(2019, 12, 31, tzinfo=timezone.utc)),
    Period("2020-Q1", datetime(2020, 1, 1, tzinfo=timezone.utc), datetime(2020, 3, 31, tzinfo=timezone.utc)),
    Period("2020-Q2", datetime(2020, 4, 1, tzinfo=timezone.utc), datetime(2020, 6, 30, tzinfo=timezone.utc)),
    Period("2020-Q3", datetime(2020, 7, 1, tzinfo=timezone.utc), datetime(2020, 9, 30, tzinfo=timezone.utc)),
    Period("2020-Q4", datetime(2020, 10, 1, tzinfo=timezone.utc), datetime(2020, 12, 31, tzinfo=timezone.utc)),
)
VERIFICATION_PERIODS = tuple(
    Period(str(year), datetime(year, 1, 1, tzinfo=timezone.utc), datetime(year, 12, 31, tzinfo=timezone.utc))
    for year in range(2021, 2026)
)


@dataclass(frozen=True)
class Features:
    return_1: float
    return_5: float
    return_20: float
    return_60: float
    return_120: float
    return_200: float
    volatility_20: float
    sma_50: float
    sma_100: float
    sma_200: float
    close: float
    drawdown_20: float
    trend_score: float


@dataclass
class SimulationResult:
    net_return: float
    cash_benchmark_return: float
    excess_return: float
    maximum_drawdown: float
    crypto_turnover: float
    crypto_action_days: int
    selected_assets: list[str]
    daily_returns: list[float]
    asset_contribution: dict[str, float]
    crypto_contribution: float
    cash_contribution: float


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
        requests[f"exit:{asset}:2026-01-01"] = (
            f"{BASE_URL}/spot/daily/klines/{symbol}/1d/{symbol}-1d-2026-01-01.zip"
        )
    return requests


def _download_fred() -> tuple[bytes, dict[str, str]]:
    request = Request(FRED_URL, headers={"User-Agent": "tradebot-v31-yield-trend/1.0"})
    with urlopen(request, timeout=90.0) as response:  # noqa: S310 - frozen public FRED URL
        if response.status != 200:
            raise HistoricalYieldTrendV31Error(
                f"FRED returned HTTP {response.status}"
            )
        content = response.read()
    return content, {
        "key": "cash:DGS3MO",
        "url": FRED_URL,
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def download_inputs(
    max_workers: int = 16,
) -> tuple[dict[str, v25.DownloadedArchive], bytes, list[dict[str, str]]]:
    requests = _archive_requests()
    downloaded: dict[str, v25.DownloadedArchive] = {}
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        pending = {
            executor.submit(v25._download, url): (key, url)
            for key, url in requests.items()
        }
        fred_future = executor.submit(_download_fred)
        for future in as_completed(pending):
            key, url = pending[future]
            try:
                downloaded[key] = future.result()
            except v25.HistoricalProxyScreenError as exc:
                failures.append({"key": key, "url": url, "reason": str(exc)})
        try:
            fred_content, fred_inventory = fred_future.result()
        except Exception as exc:  # noqa: BLE001 - normalize public source failure
            raise HistoricalYieldTrendV31Error(
                f"FRED download failed: {exc}"
            ) from exc
    if failures:
        sample = "; ".join(
            item["key"]
            for item in sorted(failures, key=lambda row: row["key"])[:8]
        )
        raise HistoricalYieldTrendV31Error(
            f"{len(failures)} crypto archives failed: {sample}"
        )
    inventory = [
        {"key": key, "url": archive.url, "sha256": archive.sha256}
        for key, archive in sorted(downloaded.items())
    ]
    inventory.append(fred_inventory)
    inventory.sort(key=lambda row: row["key"])
    return downloaded, fred_content, inventory


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
                raise HistoricalYieldTrendV31Error(
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
            raise HistoricalYieldTrendV31Error(
                f"{asset} missing {len(missing)} required bars; first={_utc(missing[0])}"
            )
    return bars, dates


def parse_cash_rates(content: bytes) -> dict[datetime, float]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HistoricalYieldTrendV31Error("FRED CSV is not UTF-8") from exc
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "DATE" not in rows[0] or "DGS3MO" not in rows[0]:
        raise HistoricalYieldTrendV31Error("FRED CSV columns unavailable")
    rates: dict[datetime, float] = {}
    for row in rows:
        raw = str(row.get("DGS3MO", "")).strip()
        if not raw or raw == ".":
            continue
        try:
            value = float(raw) / 100.0
            day = datetime.fromisoformat(str(row["DATE"])).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError) as exc:
            raise HistoricalYieldTrendV31Error(
                f"Invalid FRED row: {row}"
            ) from exc
        if value <= -1.0:
            raise HistoricalYieldTrendV31Error(
                f"Invalid annual cash rate on {_utc(day)}: {value}"
            )
        rates[day] = value
    if not rates:
        raise HistoricalYieldTrendV31Error("FRED CSV contains no rates")
    return rates


def build_daily_cash_returns(
    rates: dict[datetime, float],
    dates: list[datetime],
) -> dict[datetime, float]:
    observations = sorted(rates)
    result: dict[datetime, float] = {}
    index = -1
    latest: float | None = None
    for day in dates:
        cutoff = day - timedelta(days=1)
        while index + 1 < len(observations) and observations[index + 1] <= cutoff:
            index += 1
            latest = rates[observations[index]]
        if latest is None:
            raise HistoricalYieldTrendV31Error(
                f"No cash rate known by {_utc(cutoff)}"
            )
        result[day] = (1.0 + latest) ** (1.0 / 365.0) - 1.0
    return result


def _return(closes: list[float], index: int, lag: int) -> float:
    return closes[index] / closes[index - lag] - 1.0


def _sma(closes: list[float], index: int, length: int) -> float:
    values = closes[index - length + 1 : index + 1]
    if len(values) != length:
        raise HistoricalYieldTrendV31Error(
            f"SMA{length} unavailable at index {index}"
        )
    return sum(values) / length


def _volatility(closes: list[float], index: int, length: int = 20) -> float:
    values = closes[index - length : index + 1]
    returns = [
        values[offset] / values[offset - 1] - 1.0
        for offset in range(1, len(values))
    ]
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
            r20 = _return(series, index, 20)
            r60 = _return(series, index, 60)
            r120 = _return(series, index, 120)
            volatility = _volatility(series, index)
            high20 = max(series[index - 19 : index + 1])
            daily[asset] = Features(
                return_1=_return(series, index, 1),
                return_5=_return(series, index, 5),
                return_20=r20,
                return_60=r60,
                return_120=r120,
                return_200=_return(series, index, 200),
                volatility_20=volatility,
                sma_50=_sma(series, index, 50),
                sma_100=_sma(series, index, 100),
                sma_200=_sma(series, index, 200),
                close=series[index],
                drawdown_20=series[index] / high20 - 1.0,
                trend_score=(
                    0.50 * r20 + 0.30 * r60 + 0.20 * r120
                ) / max(volatility, 0.015),
            )
        payload[day] = daily
    return payload


def _selected_sma(item: Features, length: int) -> float:
    if length == 100:
        return item.sma_100
    if length == 200:
        return item.sma_200
    raise HistoricalYieldTrendV31Error(f"Unsupported SMA: {length}")


def _target(
    model: ModelSpec,
    features: dict[str, Features],
    prior_assets: tuple[str, ...],
    prior_sleeve: str,
    age: int,
) -> tuple[dict[str, float], tuple[str, ...], str, int]:
    btc = features["BTC"]
    flags = {
        asset: (
            item.close > _selected_sma(item, model.sma_length)
            and item.return_60 > 0.0
            and item.return_120 > 0.0
        )
        for asset, item in features.items()
    }
    risk_on = (
        flags["BTC"]
        and any(flags.values())
    )
    if not risk_on:
        return {}, (), "cash", 0
    due = prior_sleeve != "trend" or age >= model.rebalance_days - 1
    selected = prior_assets
    next_age = age + 1
    if due:
        ranked: list[tuple[float, str]] = []
        for asset, item in features.items():
            if (
                not flags[asset]
                or item.return_20 <= 0.0
                or item.return_200 <= 0.0
                or item.trend_score <= 0.0
                or item.return_1 > 0.08
                or item.return_5 > 0.20
            ):
                continue
            ranked.append((item.trend_score, asset))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        selected = tuple(asset for _, asset in ranked[: model.top_n])
        next_age = 0
    if not selected:
        return {}, (), "cash", 0
    selected_volatility = median(
        [features[asset].volatility_20 for asset in selected]
    )
    exposure = model.maximum_exposure * min(
        1.0,
        model.volatility_target / max(selected_volatility, 1e-12),
    )
    if btc.drawdown_20 <= -model.drawdown_brake:
        exposure *= 0.5
    exposure = max(0.0, min(model.maximum_exposure, 0.20, exposure))
    if exposure <= 0.0:
        return {}, (), "cash", 0
    weight = exposure / len(selected)
    return (
        {asset: weight for asset in selected},
        selected,
        "trend",
        next_age,
    )


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
    cash_returns: dict[datetime, float],
    start: datetime,
    end: datetime,
    cost: float,
) -> SimulationResult:
    current_weights: dict[str, float] = {}
    selected_assets: tuple[str, ...] = ()
    sleeve = "cash"
    age = 0
    daily_returns: list[float] = []
    cash_benchmark_returns: list[float] = []
    turnover_total = 0.0
    action_days = 0
    used_assets: set[str] = set()
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    crypto_contribution = 0.0
    cash_contribution = 0.0
    day = start
    while day <= end:
        signal_day = day - timedelta(days=1)
        next_day = day + timedelta(days=1)
        if signal_day not in features:
            raise HistoricalYieldTrendV31Error(
                f"Features unavailable for {_utc(signal_day)}"
            )
        target, selected_assets, next_sleeve, age = _target(
            model,
            features[signal_day],
            selected_assets,
            sleeve,
            age,
        )
        exposure = sum(target.values())
        if exposure > model.maximum_exposure + 1e-12 or exposure > 0.20 + 1e-12:
            raise HistoricalYieldTrendV31Error("Crypto exposure cap violated")
        turnover_by_asset = {
            asset: abs(
                target.get(asset, 0.0) - current_weights.get(asset, 0.0)
            )
            for asset in ASSETS
        }
        turnover = sum(turnover_by_asset.values())
        trading_cost = 0.5 * cost * turnover
        if turnover > 1e-10 and target:
            action_days += 1
        turnover_total += turnover
        used_assets.update(target)

        cash_return = cash_returns[day]
        cash_weight = 1.0 - exposure
        day_cash = cash_weight * cash_return
        day_crypto = 0.0
        per_asset: dict[str, float] = {}
        for asset, weight in target.items():
            raw = bars[asset][next_day].open / bars[asset][day].open - 1.0
            value = weight * raw
            per_asset[asset] = value
            day_crypto += value
        net = day_cash + day_crypto - trading_cost
        daily_returns.append(net)
        cash_benchmark_returns.append(cash_return)
        cash_contribution += day_cash
        crypto_contribution += day_crypto - trading_cost

        traded_total = sum(turnover_by_asset.values())
        for asset in ASSETS:
            allocated_cost = (
                trading_cost * turnover_by_asset[asset] / traded_total
                if traded_total > 0.0
                else 0.0
            )
            asset_contribution[asset] += per_asset.get(asset, 0.0) - allocated_cost

        denominator = 1.0 + net
        if denominator <= 0.0:
            raise HistoricalYieldTrendV31Error("Portfolio equity became nonpositive")
        current_weights = {
            asset: weight
            * (bars[asset][next_day].open / bars[asset][day].open)
            / denominator
            for asset, weight in target.items()
        }
        sleeve = next_sleeve
        day += timedelta(days=1)

    final_turnover = sum(current_weights.values())
    if final_turnover > 1e-12:
        final_cost = 0.5 * cost * final_turnover
        daily_returns.append(-final_cost)
        turnover_total += final_turnover
        crypto_contribution -= final_cost
        for asset, weight in current_weights.items():
            asset_contribution[asset] -= final_cost * weight / final_turnover

    net_return = _compounded(daily_returns)
    cash_benchmark = _compounded(cash_benchmark_returns)
    return SimulationResult(
        net_return=net_return,
        cash_benchmark_return=cash_benchmark,
        excess_return=net_return - cash_benchmark,
        maximum_drawdown=_maximum_drawdown(daily_returns),
        crypto_turnover=turnover_total,
        crypto_action_days=action_days,
        selected_assets=sorted(used_assets),
        daily_returns=daily_returns,
        asset_contribution={
            key: value
            for key, value in sorted(asset_contribution.items())
            if abs(value) > 1e-15
        },
        crypto_contribution=crypto_contribution,
        cash_contribution=cash_contribution,
    )


def _combine(results: dict[str, SimulationResult]) -> dict[str, Any]:
    net_returns = {name: result.net_return for name, result in results.items()}
    cash_returns = {
        name: result.cash_benchmark_return for name, result in results.items()
    }
    excess_returns = {name: result.excess_return for name, result in results.items()}
    daily = [value for result in results.values() for value in result.daily_returns]
    assets = sorted({asset for result in results.values() for asset in result.selected_assets})
    asset_contribution: dict[str, float] = {}
    for result in results.values():
        for key, value in result.asset_contribution.items():
            asset_contribution[key] = asset_contribution.get(key, 0.0) + value
    positive_assets = {key: max(0.0, value) for key, value in asset_contribution.items()}
    positive_excess = {key: max(0.0, value) for key, value in excess_returns.items()}
    asset_total = sum(positive_assets.values())
    excess_total = sum(positive_excess.values())
    compounded_net = _compounded(list(net_returns.values()))
    compounded_cash = _compounded(list(cash_returns.values()))
    return {
        "net_compounded_return": compounded_net,
        "cash_benchmark_compounded_return": compounded_cash,
        "excess_compounded_return": compounded_net - compounded_cash,
        "maximum_drawdown": _maximum_drawdown(daily),
        "crypto_turnover": sum(result.crypto_turnover for result in results.values()),
        "crypto_action_days": sum(result.crypto_action_days for result in results.values()),
        "selected_assets": assets,
        "window_returns": net_returns,
        "cash_window_returns": cash_returns,
        "excess_window_returns": excess_returns,
        "window_action_days": {
            name: result.crypto_action_days for name, result in results.items()
        },
        "asset_net_contribution": dict(sorted(asset_contribution.items())),
        "crypto_contribution": sum(
            result.crypto_contribution for result in results.values()
        ),
        "cash_contribution": sum(
            result.cash_contribution for result in results.values()
        ),
        "maximum_positive_asset_share": (
            0.0
            if asset_total <= 0.0
            else max(positive_assets.values(), default=0.0) / asset_total
        ),
        "maximum_positive_excess_window_share": (
            0.0
            if excess_total <= 0.0
            else max(positive_excess.values(), default=0.0) / excess_total
        ),
    }


def _discovery_row(
    model: ModelSpec,
    bars: dict[str, dict[datetime, v25.HourlyBar]],
    features: dict[datetime, dict[str, Features]],
    cash_returns: dict[datetime, float],
) -> dict[str, Any]:
    results = {
        period.name: simulate(
            model,
            bars,
            features,
            cash_returns,
            period.start,
            period.end,
            STRESS_COST,
        )
        for period in DISCOVERY_PERIODS
    }
    combined = _combine(results)
    return {
        "model": asdict(model),
        "model_id": model.model_id,
        "positive_quarters": sum(
            value > 0.0 for value in combined["window_returns"].values()
        ),
        "quarters_beating_cash": sum(
            value > 0.0 for value in combined["excess_window_returns"].values()
        ),
        "minimum_excess_quarter": min(
            combined["excess_window_returns"].values()
        ),
        "median_excess_quarter": median(
            combined["excess_window_returns"].values()
        ),
        "compounded_stress_excess_return": combined[
            "excess_compounded_return"
        ],
        "maximum_drawdown": combined["maximum_drawdown"],
        "crypto_turnover": combined["crypto_turnover"],
        "quarter_returns": combined["window_returns"],
        "cash_quarter_returns": combined["cash_window_returns"],
        "excess_quarter_returns": combined["excess_window_returns"],
    }


def select_model(
    bars: dict[str, dict[datetime, v25.HourlyBar]],
    features: dict[datetime, dict[str, Features]],
    cash_returns: dict[datetime, float],
) -> tuple[ModelSpec, list[dict[str, Any]]]:
    table = [
        _discovery_row(model, bars, features, cash_returns)
        for model in MODEL_GRID
    ]
    table.sort(
        key=lambda row: (
            -row["positive_quarters"],
            -row["quarters_beating_cash"],
            -row["minimum_excess_quarter"],
            -row["median_excess_quarter"],
            -row["compounded_stress_excess_return"],
            row["maximum_drawdown"],
            row["crypto_turnover"],
            row["model_id"],
        )
    )
    chosen = next(
        model for model in MODEL_GRID if model.model_id == table[0]["model_id"]
    )
    return chosen, table


def run_overlay(max_workers: int = 16) -> dict[str, Any]:
    if not PROTOCOL_PATH.is_file() or not ADDENDUM_PATH.is_file():
        raise HistoricalYieldTrendV31Error("v3.1 protocol files are missing")
    if len(MODEL_GRID) != 64:
        raise HistoricalYieldTrendV31Error(
            "Frozen model grid must contain exactly 64 models"
        )
    downloaded, fred_content, inventory = download_inputs(
        max_workers=max_workers
    )
    bars, dates = assemble_bars(downloaded)
    rates = parse_cash_rates(fred_content)
    cash_returns = build_daily_cash_returns(rates, dates)
    features = build_features(bars, dates)
    chosen, selection_table = select_model(bars, features, cash_returns)

    discovery_standard_results = {
        period.name: simulate(
            chosen,
            bars,
            features,
            cash_returns,
            period.start,
            period.end,
            STANDARD_COST,
        )
        for period in DISCOVERY_PERIODS
    }
    discovery_stress_results = {
        period.name: simulate(
            chosen,
            bars,
            features,
            cash_returns,
            period.start,
            period.end,
            STRESS_COST,
        )
        for period in DISCOVERY_PERIODS
    }
    discovery_standard = _combine(discovery_standard_results)
    discovery_stress = _combine(discovery_stress_results)
    discovery_gates = {
        "eight_positive_quarters": sum(
            value > 0.0
            for value in discovery_stress["window_returns"].values()
        ) >= 8,
        "eight_quarters_beating_cash": sum(
            value > 0.0
            for value in discovery_stress["excess_window_returns"].values()
        ) >= 8,
        "positive_median_excess_quarter": median(
            discovery_stress["excess_window_returns"].values()
        ) > 0.0,
        "positive_standard_return": discovery_standard[
            "net_compounded_return"
        ] > 0.0,
        "positive_stress_return": discovery_stress[
            "net_compounded_return"
        ] > 0.0,
        "positive_stress_excess_return": discovery_stress[
            "excess_compounded_return"
        ] > 0.0,
        "minimum_stress_quarter_above_minus_three_percent": min(
            discovery_stress["window_returns"].values()
        ) > -0.03,
        "drawdown_cap": discovery_stress["maximum_drawdown"] <= 0.08,
    }

    verification_standard_results = {
        period.name: simulate(
            chosen,
            bars,
            features,
            cash_returns,
            period.start,
            period.end,
            STANDARD_COST,
        )
        for period in VERIFICATION_PERIODS
    }
    verification_stress_results = {
        period.name: simulate(
            chosen,
            bars,
            features,
            cash_returns,
            period.start,
            period.end,
            STRESS_COST,
        )
        for period in VERIFICATION_PERIODS
    }
    verification_standard = _combine(verification_standard_results)
    verification_stress = _combine(verification_stress_results)
    gates = {
        "discovery_robustness": all(discovery_gates.values()),
        "five_positive_standard_years": all(
            value > 0.0
            for value in verification_standard["window_returns"].values()
        ),
        "five_positive_stress_years": all(
            value > 0.0
            for value in verification_stress["window_returns"].values()
        ),
        "overall_standard_beats_cash": verification_standard[
            "excess_compounded_return"
        ] > 0.0,
        "overall_stress_beats_cash": verification_stress[
            "excess_compounded_return"
        ] > 0.0,
        "three_standard_years_beat_cash": sum(
            value > 0.0
            for value in verification_standard[
                "excess_window_returns"
            ].values()
        ) >= 3,
        "three_stress_years_beat_cash": sum(
            value > 0.0
            for value in verification_stress[
                "excess_window_returns"
            ].values()
        ) >= 3,
        "two_actions_each_year": all(
            value >= 2
            for value in verification_standard["window_action_days"].values()
        ),
        "twenty_actions": verification_standard["crypto_action_days"] >= 20,
        "both_assets_selected": set(verification_standard["selected_assets"])
        == set(ASSETS),
        "drawdown_cap": verification_standard["maximum_drawdown"] <= 0.08,
        "asset_concentration": verification_standard[
            "maximum_positive_asset_share"
        ] <= 0.80,
        "year_concentration": verification_standard[
            "maximum_positive_excess_window_share"
        ] <= 0.45,
        "all_inputs_complete": True,
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
        "cash_series": "DGS3MO",
        "data_start_utc": _utc(DATA_START),
        "discovery_start_utc": _utc(DISCOVERY_START),
        "discovery_end_utc": _utc(DISCOVERY_END),
        "verification_start_utc": _utc(VERIFICATION_START),
        "verification_end_utc": _utc(VERIFICATION_END),
        "model_grid_size": len(MODEL_GRID),
        "chosen_model": asdict(chosen) | {"model_id": chosen.model_id},
        "selection_table": selection_table,
        "discovery_periods": [
            {"name": item.name, "start": _utc(item.start), "end": _utc(item.end)}
            for item in DISCOVERY_PERIODS
        ],
        "verification_periods": [
            {"name": item.name, "start": _utc(item.start), "end": _utc(item.end)}
            for item in VERIFICATION_PERIODS
        ],
        "discovery": {
            "standard": discovery_standard,
            "stress": discovery_stress,
            "gates": discovery_gates,
        },
        "verification": {
            "standard": verification_standard,
            "stress": verification_stress,
            "gates": gates,
        },
        "source_inventory": inventory,
        "source_inventory_sha256": hashlib.sha256(
            canonical_json(inventory).encode("utf-8")
        ).hexdigest(),
        "fingerprints": {
            "protocol_sha256": hashlib.sha256(
                PROTOCOL_PATH.read_bytes()
            ).hexdigest(),
            "addendum_sha256": hashlib.sha256(
                ADDENDUM_PATH.read_bytes()
            ).hexdigest(),
            "implementation_sha256": hashlib.sha256(
                source_path.read_bytes()
            ).hexdigest(),
            "chosen_model_sha256": hashlib.sha256(
                canonical_json(asdict(chosen)).encode("utf-8")
            ).hexdigest(),
        },
        "screening_status": (
            "VERIFIED_FIVE_YEAR_YIELD_TREND_BREAKTHROUGH"
            if all(gates.values())
            else "NOT_VERIFIED_FIVE_YEAR_YIELD_TREND_BREAKTHROUGH"
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
        description="Run isolated v3.1 yield-bearing cash trend overlay."
    )
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--max-workers", type=int, default=16)
    args = parser.parse_args(argv)
    report = run_overlay(max_workers=args.max_workers)
    _atomic_json(Path(args.json_out), report)
    verification = report["verification"]
    print(
        json.dumps(
            {
                "status": report["screening_status"],
                "chosen_model": report["chosen_model"],
                "standard_return": verification["standard"][
                    "net_compounded_return"
                ],
                "stress_return": verification["stress"][
                    "net_compounded_return"
                ],
                "cash_return": verification["standard"][
                    "cash_benchmark_compounded_return"
                ],
                "standard_years": verification["standard"][
                    "window_returns"
                ],
                "stress_years": verification["stress"][
                    "window_returns"
                ],
                "standard_excess_years": verification["standard"][
                    "excess_window_returns"
                ],
                "maximum_drawdown": verification["standard"][
                    "maximum_drawdown"
                ],
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
