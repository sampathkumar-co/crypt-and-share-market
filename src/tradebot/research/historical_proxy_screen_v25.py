from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tradebot.research import market_state_router as v21
from tradebot.research.forward_alpha_v25 import canonical_json, evaluate_forward_alpha_v25


MODE = "HISTORICAL_PROXY_SCREEN_ONLY"
PROTOCOL_PATH = Path("research/V25_HISTORICAL_PROXY_SCREEN_PROTOCOL.md")
ASSETS = ("BTC", "ETH", "SOL", "AVAX", "LINK", "DOGE")
SYMBOLS = {asset: f"{asset}USDT" for asset in ASSETS}
WARMUP_START = datetime(2026, 5, 24, tzinfo=timezone.utc)
SCREEN_START = datetime(2026, 6, 1, tzinfo=timezone.utc)
SCREEN_END = datetime(2026, 6, 30, 23, tzinfo=timezone.utc)
EXIT_DATA_END = datetime(2026, 7, 1, 8, tzinfo=timezone.utc)
HORIZONS = (2, 4, 8)
STANDARD_COST = 0.002
STRESS_COST = 0.004
BASE_URL = "https://data.binance.vision/data"
PROXY_DISCLOSURE = {
    "spot_spread_bps": "hourly spot high-low range divided by close",
    "spot_book_notional": "hourly spot quote volume",
    "spot_book_imbalance": "spot taker imbalance",
}


class HistoricalProxyScreenError(RuntimeError):
    """Raised when the historical proxy screen cannot be reproduced safely."""


@dataclass(frozen=True)
class HourlyBar:
    hour: datetime
    open: float
    high: float
    low: float
    close: float
    quote_volume: float
    taker_buy_quote_volume: float


@dataclass(frozen=True)
class DownloadedArchive:
    url: str
    sha256: str
    content: bytes


@dataclass(frozen=True)
class ScreenEvent:
    decision_hour: datetime
    asset: str
    family: str
    weight: float
    event_key: str


def _utc_hour(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite(value: Any, field: str, *, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HistoricalProxyScreenError(f"{field} is not numeric: {value!r}") from exc
    if not math.isfinite(number):
        raise HistoricalProxyScreenError(f"{field} is not finite")
    if positive and number <= 0:
        raise HistoricalProxyScreenError(f"{field} must be positive")
    return number


def _timestamp(value: Any, field: str) -> datetime:
    text = str(value).strip()
    if not text:
        raise HistoricalProxyScreenError(f"{field} is empty")
    try:
        numeric = int(float(text))
    except ValueError:
        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise HistoricalProxyScreenError(f"{field} is not a timestamp: {text}") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    if numeric > 10**14:  # Binance spot archives use microseconds from 2025 onward.
        numeric //= 1_000
    return datetime.fromtimestamp(numeric / 1_000.0, tz=timezone.utc)


def _download(url: str, *, attempts: int = 4, timeout: float = 60.0) -> DownloadedArchive:
    last_error: Exception | None = None
    request = Request(url, headers={"User-Agent": "dual-market-ai-bot-historical-screen/2.5"})
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed public archive host
                if response.status != 200:
                    raise HistoricalProxyScreenError(f"HTTP {response.status}: {url}")
                content = response.read()
            return DownloadedArchive(url, hashlib.sha256(content).hexdigest(), content)
        except (HTTPError, URLError, TimeoutError, HistoricalProxyScreenError) as exc:
            last_error = exc
            if isinstance(exc, HTTPError) and exc.code == 404:
                break
            if attempt < attempts:
                time.sleep(float(attempt))
    raise HistoricalProxyScreenError(f"Archive download failed: {url}: {last_error}")


def _csv_rows(archive: DownloadedArchive) -> list[list[str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
            names = [name for name in bundle.namelist() if name.lower().endswith(".csv")]
            if len(names) != 1:
                raise HistoricalProxyScreenError(
                    f"Expected one CSV in {archive.url}, found {len(names)}"
                )
            raw = bundle.read(names[0]).decode("utf-8-sig")
    except (zipfile.BadZipFile, UnicodeDecodeError, KeyError) as exc:
        raise HistoricalProxyScreenError(f"Unreadable archive {archive.url}: {exc}") from exc
    rows = [row for row in csv.reader(io.StringIO(raw)) if row]
    if not rows:
        raise HistoricalProxyScreenError(f"Archive contains no CSV rows: {archive.url}")
    return rows


def _looks_like_header(row: list[str]) -> bool:
    try:
        float(row[0])
        return False
    except (ValueError, IndexError):
        return True


def _parse_klines(archive: DownloadedArchive) -> dict[datetime, HourlyBar]:
    rows = _csv_rows(archive)
    if _looks_like_header(rows[0]):
        rows = rows[1:]
    bars: dict[datetime, HourlyBar] = {}
    for index, row in enumerate(rows):
        if len(row) < 11:
            raise HistoricalProxyScreenError(f"Malformed kline row {index} in {archive.url}")
        hour = _timestamp(row[0], "kline.open_time").replace(minute=0, second=0, microsecond=0)
        bar = HourlyBar(
            hour=hour,
            open=_finite(row[1], "kline.open", positive=True),
            high=_finite(row[2], "kline.high", positive=True),
            low=_finite(row[3], "kline.low", positive=True),
            close=_finite(row[4], "kline.close", positive=True),
            quote_volume=_finite(row[7], "kline.quote_volume"),
            taker_buy_quote_volume=_finite(row[10], "kline.taker_buy_quote_volume"),
        )
        if bar.low > bar.high or not (bar.low <= bar.open <= bar.high) or not (bar.low <= bar.close <= bar.high):
            raise HistoricalProxyScreenError(f"Invalid OHLC relationship at {hour} in {archive.url}")
        prior = bars.get(hour)
        if prior is not None and prior != bar:
            raise HistoricalProxyScreenError(f"Conflicting kline hour {hour} in {archive.url}")
        bars[hour] = bar
    return bars


def _header_map(row: list[str]) -> dict[str, int]:
    return {value.strip().lower(): index for index, value in enumerate(row)}


def _find_column(header: dict[str, int], choices: Iterable[str]) -> int | None:
    for name in choices:
        if name.lower() in header:
            return header[name.lower()]
    return None


def _parse_funding(archive: DownloadedArchive) -> dict[datetime, float]:
    rows = _csv_rows(archive)
    header = _header_map(rows[0]) if _looks_like_header(rows[0]) else {}
    data = rows[1:] if header else rows
    time_index = _find_column(header, ("calc_time", "fundingtime", "funding_time")) if header else 0
    rate_index = _find_column(header, ("last_funding_rate", "fundingrate", "funding_rate")) if header else 2
    if time_index is None or rate_index is None:
        raise HistoricalProxyScreenError(f"Funding columns are unavailable in {archive.url}")
    values: dict[datetime, float] = {}
    for row in data:
        if max(time_index, rate_index) >= len(row):
            continue
        timestamp = _timestamp(row[time_index], "funding.time")
        values[timestamp] = _finite(row[rate_index], "funding.rate")
    if not values:
        raise HistoricalProxyScreenError(f"No funding values found in {archive.url}")
    return values


def _parse_metrics(archive: DownloadedArchive) -> dict[datetime, float]:
    rows = _csv_rows(archive)
    if not _looks_like_header(rows[0]):
        raise HistoricalProxyScreenError(f"Metrics archive has no header: {archive.url}")
    header = _header_map(rows[0])
    time_index = _find_column(header, ("create_time", "timestamp", "time"))
    oi_index = _find_column(header, ("sum_open_interest", "open_interest", "openinterest"))
    if time_index is None or oi_index is None:
        raise HistoricalProxyScreenError(f"Metrics columns are unavailable in {archive.url}")
    latest: dict[datetime, tuple[datetime, float]] = {}
    for row in rows[1:]:
        if max(time_index, oi_index) >= len(row):
            continue
        observed = _timestamp(row[time_index], "metrics.time")
        hour = observed.replace(minute=0, second=0, microsecond=0)
        value = _finite(row[oi_index], "metrics.open_interest", positive=True)
        prior = latest.get(hour)
        if prior is None or observed > prior[0]:
            latest[hour] = (observed, value)
    return {hour: value for hour, (_, value) in latest.items()}


def _months(start: datetime, end: datetime) -> list[str]:
    current = date(start.year, start.month, 1)
    finish = date(end.year, end.month, 1)
    result: list[str] = []
    while current <= finish:
        result.append(current.strftime("%Y-%m"))
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return result


def _days(start: datetime, end: datetime) -> list[str]:
    current = start.date()
    finish = end.date()
    result: list[str] = []
    while current <= finish:
        result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def _archive_urls() -> dict[str, tuple[str, str, str | None]]:
    requests: dict[str, tuple[str, str, str | None]] = {}
    for asset, symbol in SYMBOLS.items():
        for month in _months(WARMUP_START, SCREEN_END):
            requests[f"spot:{asset}:{month}"] = (
                "spot_kline",
                asset,
                f"{BASE_URL}/spot/monthly/klines/{symbol}/1h/{symbol}-1h-{month}.zip",
            )
            requests[f"futures:{asset}:{month}"] = (
                "futures_kline",
                asset,
                f"{BASE_URL}/futures/um/monthly/klines/{symbol}/1h/{symbol}-1h-{month}.zip",
            )
            requests[f"funding:{asset}:{month}"] = (
                "funding",
                asset,
                f"{BASE_URL}/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip",
            )
        for day in _days(WARMUP_START, SCREEN_END):
            requests[f"metrics:{asset}:{day}"] = (
                "metrics",
                asset,
                f"{BASE_URL}/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{day}.zip",
            )
        requests[f"exit:{asset}:2026-07-01"] = (
            "exit_spot_kline",
            asset,
            f"{BASE_URL}/spot/daily/klines/{symbol}/1h/{symbol}-1h-2026-07-01.zip",
        )
    return requests


def download_historical_inputs(max_workers: int = 8) -> tuple[dict[str, DownloadedArchive], list[dict[str, str]]]:
    requests = _archive_urls()
    downloaded: dict[str, DownloadedArchive] = {}
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        pending = {executor.submit(_download, url or ""): (key, url or "") for key, (_, _, url) in requests.items()}
        for future in as_completed(pending):
            key, url = pending[future]
            try:
                downloaded[key] = future.result()
            except HistoricalProxyScreenError as exc:
                failures.append({"key": key, "url": url, "reason": str(exc)})
    if failures:
        sample = "; ".join(item["key"] for item in failures[:5])
        raise HistoricalProxyScreenError(f"{len(failures)} required archives failed: {sample}")
    inventory = [
        {"key": key, "url": archive.url, "sha256": archive.sha256}
        for key, archive in sorted(downloaded.items())
    ]
    return downloaded, inventory


def _latest_known(values: dict[datetime, float], hour: datetime) -> float | None:
    eligible = [timestamp for timestamp in values if timestamp <= hour + timedelta(hours=1) - timedelta(microseconds=1)]
    return None if not eligible else values[max(eligible)]


def _imbalance(total: float, taker_buy: float) -> float:
    if total <= 0:
        return 0.0
    return max(-1.0, min(1.0, 2.0 * taker_buy / total - 1.0))


def assemble_inputs(downloaded: dict[str, DownloadedArchive]) -> tuple[list[v21.SnapshotFrame], dict[str, dict[datetime, HourlyBar]], list[dict[str, str]]]:
    spot: dict[str, dict[datetime, HourlyBar]] = {asset: {} for asset in ASSETS}
    futures: dict[str, dict[datetime, HourlyBar]] = {asset: {} for asset in ASSETS}
    funding: dict[str, dict[datetime, float]] = {asset: {} for asset in ASSETS}
    open_interest: dict[str, dict[datetime, float]] = {asset: {} for asset in ASSETS}
    excluded: list[dict[str, str]] = []

    for key, archive in sorted(downloaded.items()):
        kind, asset, _ = _archive_urls()[key]
        if kind in {"spot_kline", "exit_spot_kline"}:
            parsed = _parse_klines(archive)
            target = spot[asset]
            for hour, bar in parsed.items():
                if hour in target and target[hour] != bar:
                    raise HistoricalProxyScreenError(f"Conflicting spot bar {asset} {hour}")
                target[hour] = bar
        elif kind == "futures_kline":
            parsed = _parse_klines(archive)
            target = futures[asset]
            for hour, bar in parsed.items():
                if hour in target and target[hour] != bar:
                    raise HistoricalProxyScreenError(f"Conflicting futures bar {asset} {hour}")
                target[hour] = bar
        elif kind == "funding":
            funding[asset].update(_parse_funding(archive))
        elif kind == "metrics":
            open_interest[asset].update(_parse_metrics(archive))

    frames: list[v21.SnapshotFrame] = []
    hour = WARMUP_START
    while hour <= SCREEN_END:
        assets_payload: dict[str, Any] = {}
        reasons: list[str] = []
        for asset in ASSETS:
            spot_bar = spot[asset].get(hour)
            futures_bar = futures[asset].get(hour)
            oi = open_interest[asset].get(hour)
            funding_rate = _latest_known(funding[asset], hour)
            if spot_bar is None:
                reasons.append(f"{asset}:missing_spot_kline")
                continue
            if futures_bar is None:
                reasons.append(f"{asset}:missing_futures_kline")
                continue
            if oi is None:
                reasons.append(f"{asset}:missing_open_interest")
                continue
            if funding_rate is None:
                reasons.append(f"{asset}:missing_funding")
                continue
            spot_imbalance = _imbalance(spot_bar.quote_volume, spot_bar.taker_buy_quote_volume)
            perp_imbalance = _imbalance(futures_bar.quote_volume, futures_bar.taker_buy_quote_volume)
            spread_proxy = (spot_bar.high - spot_bar.low) / spot_bar.close * 10_000.0
            total_notional = max(spot_bar.quote_volume, 0.0)
            bid_notional = total_notional * (1.0 + spot_imbalance) / 2.0
            ask_notional = total_notional - bid_notional
            assets_payload[asset] = {
                "spot_quote": {"available": True, "mid": spot_bar.close},
                "spot_book": {
                    "available": True,
                    "spread_bps": spread_proxy,
                    "bid_notional": bid_notional,
                    "ask_notional": ask_notional,
                    "imbalance": spot_imbalance,
                    "historical_proxy": True,
                },
                "spot_trade_flow": {"available": True, "taker_imbalance": spot_imbalance},
                "perp_state": {
                    "available": True,
                    "funding": funding_rate,
                    "open_interest_base": oi,
                },
                "perp_trade_flow": {"available": True, "reported_side_imbalance": perp_imbalance},
                "cross_venue": {
                    "available": True,
                    "spot_perp_basis_bps": (futures_bar.close / spot_bar.close - 1.0) * 10_000.0,
                },
            }
        if reasons:
            excluded.append({"hour": _utc_hour(hour), "reason": ",".join(reasons)})
        else:
            snapshot_id = f"historical-proxy-{hour.strftime('%Y%m%dT%H0000Z')}"
            payload = {
                "hour": _utc_hour(hour),
                "snapshot_id": snapshot_id,
                "assets": assets_payload,
                "proxy_disclosure": PROXY_DISCLOSURE,
            }
            digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
            frames.append(v21.SnapshotFrame(
                hour=hour,
                captured_at=hour + timedelta(minutes=59, seconds=59),
                snapshot_id=snapshot_id,
                record_sha256=digest,
                assets=assets_payload,
                global_state={},
                source_path=f"historical-proxy://{snapshot_id}",
            ))
        hour += timedelta(hours=1)
    return frames, spot, excluded


def build_events(frames: list[v21.SnapshotFrame]) -> tuple[list[ScreenEvent], list[dict[str, Any]]]:
    events: list[ScreenEvent] = []
    decisions: list[dict[str, Any]] = []
    last_event: dict[tuple[str, str], datetime] = {}
    current = SCREEN_START
    while current <= SCREEN_END:
        report = evaluate_forward_alpha_v25(frames, as_of=current)
        decisions.append({
            "hour": _utc_hour(current),
            "candidate_state": report["candidate_state"],
            "decision_reason": report["decision_reason"],
            "selected_candidates": report["selected_candidates"],
            "report_sha256": report["report_sha256"],
        })
        for candidate in report["selected_candidates"]:
            key = (str(candidate["asset"]), str(candidate["family"]))
            previous = last_event.get(key)
            if previous is not None and current - previous < timedelta(hours=4):
                continue
            event = ScreenEvent(
                decision_hour=current,
                asset=key[0],
                family=key[1],
                weight=float(candidate["target_weight"]),
                event_key=str(candidate["event_key"]),
            )
            events.append(event)
            last_event[key] = current
        current += timedelta(hours=1)
    return events, decisions


def _maximum_drawdown(returns: list[float]) -> float:
    equity = peak = 1.0
    maximum = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        maximum = max(maximum, 0.0 if peak <= 0 else 1.0 - equity / peak)
    return maximum


def _compounded(returns: list[float]) -> float:
    equity = 1.0
    for value in returns:
        equity *= 1.0 + value
    return equity - 1.0


def evaluate_events(events: list[ScreenEvent], spot: dict[str, dict[datetime, HourlyBar]], horizon: int, cost: float) -> dict[str, Any]:
    cohorts: dict[datetime, list[ScreenEvent]] = {}
    for event in events:
        cohorts.setdefault(event.decision_hour, []).append(event)
    cohort_returns: list[float] = []
    gross_returns: list[float] = []
    benchmark_btc: list[float] = []
    benchmark_equal: list[float] = []
    contributions_asset: dict[str, float] = {}
    contributions_family: dict[str, float] = {}
    accepted = 0
    missing: list[dict[str, str]] = []
    event_wins = 0

    for decision_hour in sorted(cohorts):
        entry_hour = decision_hour + timedelta(hours=1)
        exit_hour = entry_hour + timedelta(hours=horizon)
        selected = cohorts[decision_hour]
        cohort_gross = cohort_net = 0.0
        valid = True
        for event in selected:
            entry = spot[event.asset].get(entry_hour)
            exit_bar = spot[event.asset].get(exit_hour)
            if entry is None or exit_bar is None:
                missing.append({"hour": _utc_hour(decision_hour), "reason": f"missing_exit_price:{event.asset}"})
                valid = False
                break
            raw = exit_bar.open / entry.open - 1.0
            net = raw - cost
            weighted_gross = event.weight * raw
            weighted_net = event.weight * net
            cohort_gross += weighted_gross
            cohort_net += weighted_net
            contributions_asset[event.asset] = contributions_asset.get(event.asset, 0.0) + weighted_net
            contributions_family[event.family] = contributions_family.get(event.family, 0.0) + weighted_net
            event_wins += int(net > 0)
            accepted += 1
        if not valid:
            continue
        btc_entry = spot["BTC"].get(entry_hour)
        btc_exit = spot["BTC"].get(exit_hour)
        equal_entries = [spot[asset].get(entry_hour) for asset in ASSETS]
        equal_exits = [spot[asset].get(exit_hour) for asset in ASSETS]
        if btc_entry is None or btc_exit is None or any(value is None for value in equal_entries + equal_exits):
            missing.append({"hour": _utc_hour(decision_hour), "reason": "missing_benchmark_price"})
            continue
        btc_return = 0.30 * (btc_exit.open / btc_entry.open - 1.0 - cost)
        equal_return = 0.30 * sum(
            exit_bar.open / entry_bar.open - 1.0 - cost
            for entry_bar, exit_bar in zip(equal_entries, equal_exits, strict=True)
            if entry_bar is not None and exit_bar is not None
        ) / len(ASSETS)
        gross_returns.append(cohort_gross)
        cohort_returns.append(cohort_net)
        benchmark_btc.append(btc_return)
        benchmark_equal.append(equal_return)

    active_days = sorted({event.decision_hour.date().isoformat() for event in events})
    midpoint = len(cohort_returns) // 2
    halves = [
        _compounded(cohort_returns[:midpoint]),
        _compounded(cohort_returns[midpoint:]),
    ] if cohort_returns else [0.0, 0.0]
    return {
        "horizon_hours": horizon,
        "round_trip_cost": cost,
        "accepted_event_count": accepted,
        "cohort_count": len(cohort_returns),
        "active_day_count": len(active_days),
        "active_days_utc": active_days,
        "gross_compounded_return": _compounded(gross_returns),
        "net_compounded_return": _compounded(cohort_returns),
        "maximum_drawdown": _maximum_drawdown(cohort_returns),
        "event_win_rate": 0.0 if accepted == 0 else event_wins / accepted,
        "chronological_half_returns": halves,
        "btc_30pct_benchmark_return": _compounded(benchmark_btc),
        "equal_weight_30pct_benchmark_return": _compounded(benchmark_equal),
        "asset_net_contribution": dict(sorted(contributions_asset.items())),
        "family_net_contribution": dict(sorted(contributions_family.items())),
        "excluded_cohorts": missing,
    }


def run_screen(max_workers: int = 8) -> dict[str, Any]:
    if not PROTOCOL_PATH.is_file():
        raise HistoricalProxyScreenError(f"Protocol is missing: {PROTOCOL_PATH}")
    downloaded, source_inventory = download_historical_inputs(max_workers=max_workers)
    frames, spot, excluded_hours = assemble_inputs(downloaded)
    events, decisions = build_events(frames)
    results = {
        str(horizon): {
            "standard": evaluate_events(events, spot, horizon, STANDARD_COST),
            "stress": evaluate_events(events, spot, horizon, STRESS_COST),
        }
        for horizon in HORIZONS
    }
    primary = results["4"]["standard"]
    stress = results["4"]["stress"]
    families = sorted({event.family for event in events})
    assets = sorted({event.asset for event in events})
    gates = {
        "positive_standard_return": primary["net_compounded_return"] > 0.0,
        "non_negative_stress_return": stress["net_compounded_return"] >= 0.0,
        "minimum_events": primary["accepted_event_count"] >= 20,
        "minimum_active_days": primary["active_day_count"] >= 7,
        "drawdown_cap": primary["maximum_drawdown"] <= 0.10,
        "beat_cash": primary["net_compounded_return"] > 0.0,
        "beat_btc_benchmark": primary["net_compounded_return"] > primary["btc_30pct_benchmark_return"],
        "beat_equal_weight_benchmark": primary["net_compounded_return"] > primary["equal_weight_30pct_benchmark_return"],
        "positive_both_halves": all(value > 0.0 for value in primary["chronological_half_returns"]),
        "minimum_active_families": len(families) >= 2,
        "minimum_active_assets": len(assets) >= 3,
    }
    source_path = Path(__file__).resolve()
    report: dict[str, Any] = {
        "schema_version": "2.5-historical-proxy-screen",
        "mode": MODE,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "changes_track_a": False,
        "screen_start_utc": _utc_hour(SCREEN_START),
        "screen_end_utc": _utc_hour(SCREEN_END),
        "warmup_start_utc": _utc_hour(WARMUP_START),
        "assets": list(ASSETS),
        "proxy_disclosure": PROXY_DISCLOSURE,
        "source_inventory": source_inventory,
        "source_inventory_sha256": hashlib.sha256(canonical_json(source_inventory).encode("utf-8")).hexdigest(),
        "excluded_hour_count": len(excluded_hours),
        "excluded_hours": excluded_hours,
        "normalized_frame_count": len(frames),
        "decision_count": len(decisions),
        "decision_inventory_sha256": hashlib.sha256(canonical_json(decisions).encode("utf-8")).hexdigest(),
        "event_count": len(events),
        "active_families": families,
        "active_assets": assets,
        "results": results,
        "screening_gates": gates,
        "screening_status": "ENCOURAGING_HISTORICAL_PROXY" if all(gates.values()) else "NOT_ENCOURAGING_HISTORICAL_PROXY",
        "cannot_replace_forward_evidence": True,
        "fingerprints": {
            "protocol_sha256": hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest(),
            "implementation_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        },
    }
    report["report_sha256"] = hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
    return report


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the isolated v2.5 historical proxy screen.")
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--max-workers", type=int, default=8)
    args = parser.parse_args(argv)
    report = run_screen(max_workers=args.max_workers)
    _atomic_json(Path(args.json_out), report)
    primary = report["results"]["4"]["standard"]
    print(json.dumps({
        "mode": report["mode"],
        "screening_status": report["screening_status"],
        "event_count": report["event_count"],
        "four_hour_net_return": primary["net_compounded_return"],
        "four_hour_maximum_drawdown": primary["maximum_drawdown"],
        "report_sha256": report["report_sha256"],
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
