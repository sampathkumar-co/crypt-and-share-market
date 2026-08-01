from __future__ import annotations

import hashlib
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tradebot.research.historical_proxy_screen_v25 import (
    BASE_URL,
    DownloadedArchive,
    HistoricalProxyScreenError,
    _csv_rows,
    _find_column,
    _finite,
    _header_map,
    _looks_like_header,
    _parse_funding,
    _timestamp,
)

ASSETS = ("BTC", "ETH", "SOL", "XRP", "ADA")
SYMBOLS = {asset: f"{asset}USDT" for asset in ASSETS}
START = datetime(2021, 1, 1, tzinfo=timezone.utc)
END = datetime(2024, 12, 31, tzinfo=timezone.utc)
CACHE_ROOT = Path(".cache/v42-binance")


class RegimeRankingSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class DailyBar:
    day: datetime
    open: float
    high: float
    low: float
    close: float
    quote_volume: float
    taker_buy_quote_volume: float


@dataclass(frozen=True)
class DailyAssetState:
    day: datetime
    spot: DailyBar
    perp: DailyBar
    funding: float
    open_interest: float
    basis: float
    spot_flow: float
    perp_flow: float


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def months(start: datetime = START, end: datetime = END) -> list[str]:
    current = date(start.year, start.month, 1)
    finish = date(end.year, end.month, 1)
    result: list[str] = []
    while current <= finish:
        result.append(current.strftime("%Y-%m"))
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return result


def days(start: datetime = START, end: datetime = END) -> list[str]:
    current = start.date()
    finish = end.date()
    result: list[str] = []
    while current <= finish:
        result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def _cache_paths(url: str) -> tuple[Path, Path]:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return CACHE_ROOT / f"{digest}.zip", CACHE_ROOT / f"{digest}.missing"


def cached_download(
    url: str,
    *,
    optional_404: bool = False,
    timeout: float = 25.0,
    attempts: int = 3,
) -> DownloadedArchive | None:
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    content_path, missing_path = _cache_paths(url)
    if content_path.is_file():
        content = content_path.read_bytes()
        return DownloadedArchive(url, hashlib.sha256(content).hexdigest(), content)
    if optional_404 and missing_path.is_file():
        return None
    last_error: Exception | None = None
    request = Request(url, headers={"User-Agent": "tradebot-v42-paper-research/1.0"})

    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                if response.status != 200:
                    raise RegimeRankingSourceError(f"HTTP {response.status}: {url}")
                content = response.read()
            if not content:
                raise RegimeRankingSourceError(f"empty archive: {url}")
            temporary = content_path.with_suffix(".tmp")
            temporary.write_bytes(content)
            temporary.replace(content_path)
            return DownloadedArchive(url, hashlib.sha256(content).hexdigest(), content)
        except HTTPError as exc:
            if optional_404 and exc.code == 404:
                missing_path.write_text(url + "\n", encoding="utf-8")
                return None
            last_error = exc
        except Exception as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(0.4 * (attempt + 1))
    raise RegimeRankingSourceError(f"download failed: {url}: {last_error}")


def parse_daily_klines(archive: DownloadedArchive) -> dict[datetime, DailyBar]:
    rows = _csv_rows(archive)
    if not rows:
        raise RegimeRankingSourceError(f"empty kline archive: {archive.url}")
    if _looks_like_header(rows[0]):
        rows = rows[1:]
    result: dict[datetime, DailyBar] = {}
    for index, row in enumerate(rows):
        if len(row) < 11:
            raise RegimeRankingSourceError(f"malformed kline row {index}: {archive.url}")
        stamp = _timestamp(row[0], "kline.open_time").replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        bar = DailyBar(
            stamp,
            _finite(row[1], "kline.open", positive=True),
            _finite(row[2], "kline.high", positive=True),
            _finite(row[3], "kline.low", positive=True),
            _finite(row[4], "kline.close", positive=True),
            _finite(row[7], "kline.quote_volume"),
            _finite(row[10], "kline.taker_buy_quote_volume"),
        )
        if bar.low > bar.high or not bar.low <= bar.open <= bar.high:
            raise RegimeRankingSourceError(f"invalid kline OHLC: {archive.url}")
        if not bar.low <= bar.close <= bar.high:
            raise RegimeRankingSourceError(f"invalid kline close: {archive.url}")
        prior = result.get(stamp)
        if prior is not None and prior != bar:
            raise RegimeRankingSourceError(f"conflicting daily kline: {stamp}")
        result[stamp] = bar
    return result


def aggregate_daily_funding(archive: DownloadedArchive) -> dict[datetime, float]:
    values = _parse_funding(archive)
    result: dict[datetime, float] = {}
    for stamp, rate in values.items():
        day = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
        result[day] = result.get(day, 0.0) + float(rate)
    return result


def parse_daily_open_interest(archive: DownloadedArchive) -> float | None:
    rows = _csv_rows(archive)
    if not rows or not _looks_like_header(rows[0]):
        raise RegimeRankingSourceError(f"metrics archive has no header: {archive.url}")
    header = _header_map(rows[0])
    time_index = _find_column(header, ("create_time", "timestamp", "time"))
    oi_index = _find_column(header, ("sum_open_interest", "open_interest", "openinterest"))
    if time_index is None or oi_index is None:
        raise RegimeRankingSourceError(f"metrics columns unavailable: {archive.url}")
    latest: tuple[datetime, float] | None = None
    for row in rows[1:]:
        if max(time_index, oi_index) >= len(row):
            continue
        try:
            observed = _timestamp(row[time_index], "metrics.time")
            value = float(row[oi_index])
        except (ValueError, HistoricalProxyScreenError):
            continue
        if not math.isfinite(value) or value <= 0.0:
            continue
        if latest is None or observed > latest[0]:
            latest = (observed, value)
    return None if latest is None else latest[1]


def _monthly_requests() -> list[tuple[str, str, str, str]]:
    result: list[tuple[str, str, str, str]] = []
    for asset, symbol in SYMBOLS.items():
        for month in months():
            result.extend([
                ("spot", asset, month, f"{BASE_URL}/spot/monthly/klines/{symbol}/1d/{symbol}-1d-{month}.zip"),
                ("perp", asset, month, f"{BASE_URL}/futures/um/monthly/klines/{symbol}/1d/{symbol}-1d-{month}.zip"),
                ("funding", asset, month, f"{BASE_URL}/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip"),
            ])
    return result


def _metrics_requests() -> list[tuple[str, str, str]]:
    return [
        (asset, day_text, f"{BASE_URL}/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{day_text}.zip")
        for asset, symbol in SYMBOLS.items()
        for day_text in days()
    ]


def load_monthly_sources(
    max_workers: int = 24,
    downloader: Callable[..., DownloadedArchive | None] = cached_download,
) -> tuple[
    dict[str, dict[datetime, DailyBar]],
    dict[str, dict[datetime, DailyBar]],
    dict[str, dict[datetime, float]],
    list[dict[str, Any]],
]:
    spot = {asset: {} for asset in ASSETS}
    perp = {asset: {} for asset in ASSETS}
    funding = {asset: {} for asset in ASSETS}
    inventory: list[dict[str, Any]] = []
    requests = _monthly_requests()
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(downloader, url): (kind, asset, month, url)
            for kind, asset, month, url in requests
        }
        for future in as_completed(futures):
            kind, asset, month, url = futures[future]
            archive = future.result()
            if archive is None:
                raise RegimeRankingSourceError(f"mandatory archive missing: {url}")
            inventory.append({
                "key": f"{kind}:{asset}:{month}",
                "url": url,
                "sha256": archive.sha256,
            })
            if kind == "spot":
                spot[asset].update(parse_daily_klines(archive))
            elif kind == "perp":
                perp[asset].update(parse_daily_klines(archive))
            else:
                funding[asset].update(aggregate_daily_funding(archive))
    return spot, perp, funding, sorted(inventory, key=lambda row: row["key"])


def load_open_interest(
    max_workers: int = 48,
    downloader: Callable[..., DownloadedArchive | None] = cached_download,
) -> tuple[dict[str, dict[datetime, float]], list[dict[str, Any]], list[dict[str, Any]]]:
    values = {asset: {} for asset in ASSETS}
    inventory: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    requests = _metrics_requests()
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(downloader, url, optional_404=True): (asset, day_text, url)
            for asset, day_text, url in requests
        }
        for future in as_completed(futures):
            asset, day_text, url = futures[future]
            archive = future.result()
            if archive is None:
                missing.append({"key": f"metrics:{asset}:{day_text}", "url": url, "reason": "404"})
                continue
            value = parse_daily_open_interest(archive)
            if value is None:
                missing.append({"key": f"metrics:{asset}:{day_text}", "url": url, "reason": "no_positive_observation"})
                continue
            day = datetime.fromisoformat(day_text).replace(tzinfo=timezone.utc)
            values[asset][day] = value
            inventory.append({
                "key": f"metrics:{asset}:{day_text}",
                "url": url,
                "sha256": archive.sha256,
            })
    return (
        values,
        sorted(inventory, key=lambda row: row["key"]),
        sorted(missing, key=lambda row: row["key"]),
    )


def flow_imbalance(bar: DailyBar) -> float:
    if bar.quote_volume <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, 2.0 * bar.taker_buy_quote_volume / bar.quote_volume - 1.0))


def assemble_states(
    spot: dict[str, dict[datetime, DailyBar]],
    perp: dict[str, dict[datetime, DailyBar]],
    funding: dict[str, dict[datetime, float]],
    open_interest: dict[str, dict[datetime, float]],
) -> tuple[dict[str, dict[datetime, DailyAssetState]], list[dict[str, str]]]:
    states = {asset: {} for asset in ASSETS}
    missing: list[dict[str, str]] = []
    expected_days = [
        datetime.fromisoformat(day_text).replace(tzinfo=timezone.utc)
        for day_text in days()
    ]
    for asset in ASSETS:
        for day in expected_days:
            reasons: list[str] = []
            if day not in spot[asset]:
                reasons.append("spot")
            if day not in perp[asset]:
                reasons.append("perp")
            if day not in funding[asset]:
                reasons.append("funding")
            if day not in open_interest[asset]:
                reasons.append("open_interest")
            if reasons:
                missing.append({
                    "asset": asset,
                    "day": day.date().isoformat(),
                    "missing": ",".join(reasons),
                })
                continue
            spot_bar = spot[asset][day]
            perp_bar = perp[asset][day]
            states[asset][day] = DailyAssetState(
                day=day,
                spot=spot_bar,
                perp=perp_bar,
                funding=float(funding[asset][day]),
                open_interest=float(open_interest[asset][day]),
                basis=perp_bar.close / spot_bar.close - 1.0,
                spot_flow=flow_imbalance(spot_bar),
                perp_flow=flow_imbalance(perp_bar),
            )
    return states, missing


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def load_all_sources(
    *,
    monthly_workers: int = 24,
    metrics_workers: int = 48,
) -> tuple[dict[str, dict[datetime, DailyAssetState]], dict[str, Any]]:
    spot, perp, funding, monthly_inventory = load_monthly_sources(monthly_workers)
    open_interest, metric_inventory, metric_missing = load_open_interest(metrics_workers)
    states, state_missing = assemble_states(spot, perp, funding, open_interest)
    inventory = monthly_inventory + metric_inventory
    report = {
        "schema_version": "4.2-binance-source-inventory",
        "source_start": START.date().isoformat(),
        "source_end": END.date().isoformat(),
        "successful_inventory_count": len(inventory),
        "missing_metric_count": len(metric_missing),
        "missing_asset_day_count": len(state_missing),
        "complete_dates_by_asset": {
            asset: len(states[asset]) for asset in ASSETS
        },
        "inventory": inventory,
        "metric_missing": metric_missing,
        "state_missing": state_missing,
    }
    report["inventory_sha256"] = hashlib.sha256(
        canonical_json(inventory).encode("utf-8")
    ).hexdigest()
    return states, report
