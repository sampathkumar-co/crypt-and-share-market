from __future__ import annotations

import argparse
import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research.historical_proxy_screen_v25 import (
    BASE_URL,
    DownloadedArchive,
    HistoricalProxyScreenError,
    _csv_rows,
    _download,
    _finite,
    _parse_funding,
    _parse_metrics,
    _timestamp,
)

MODE = "HISTORICAL_COST_AWARE_DISCOVERY_ONLY"
PROTOCOL_PATH = Path("research/V26_COST_AWARE_HISTORICAL_DISCOVERY_PROTOCOL.md")
ASSETS = ("BTC", "ETH", "SOL", "AVAX", "LINK", "DOGE")
SYMBOLS = {asset: f"{asset}USDT" for asset in ASSETS}
HORIZONS = (2, 4, 8)
STANDARD_COST = 0.002
STRESS_COST = 0.004
WEIGHT = 0.15
MIN_AMPLITUDE = 0.010
WARMUP_HOURS = 8 * 24
PRIMARY_HORIZON = 4


@dataclass(frozen=True)
class WindowSpec:
    name: str
    phase: str
    screen_start: datetime
    screen_end: datetime

    @property
    def warmup_start(self) -> datetime:
        return self.screen_start - timedelta(hours=WARMUP_HOURS)

    @property
    def data_end(self) -> datetime:
        return self.screen_end + timedelta(hours=10)


WINDOWS = (
    WindowSpec("2025-08", "discovery", datetime(2025, 8, 1, tzinfo=timezone.utc), datetime(2025, 8, 30, 23, tzinfo=timezone.utc)),
    WindowSpec("2025-11", "discovery", datetime(2025, 11, 1, tzinfo=timezone.utc), datetime(2025, 11, 30, 23, tzinfo=timezone.utc)),
    WindowSpec("2026-02", "validation", datetime(2026, 2, 1, tzinfo=timezone.utc), datetime(2026, 3, 2, 23, tzinfo=timezone.utc)),
    WindowSpec("2026-05", "validation", datetime(2026, 5, 1, tzinfo=timezone.utc), datetime(2026, 5, 30, 23, tzinfo=timezone.utc)),
)


class HistoricalDiscoveryV26Error(RuntimeError):
    """Raised when v2.6 historical discovery cannot be reproduced safely."""


@dataclass(frozen=True)
class FiveMinuteBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    quote_volume: float
    taker_buy_quote_volume: float


@dataclass(frozen=True)
class HourState:
    hour: datetime
    open: float
    high: float
    low: float
    close: float
    quote_volume: float
    taker_imbalance: float
    trend_efficiency: float
    close_location: float
    maximum_volume_share: float
    late_early_volume_ratio: float
    realized_volatility: float
    range_fraction: float


@dataclass(frozen=True)
class AssetState:
    spot: HourState
    perp: HourState
    funding: float
    open_interest: float
    basis_bps: float
    flow_lead: float


@dataclass(frozen=True)
class Candidate:
    signal_hour: datetime
    confirmation_hour: datetime
    asset: str
    family: str
    score: float
    amplitude: float
    event_key: str


@dataclass(frozen=True)
class Event:
    window: str
    phase: str
    signal_hour: datetime
    confirmation_hour: datetime
    entry_hour: datetime
    asset: str
    family: str
    score: float
    amplitude: float
    weight: float
    event_key: str


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _archive_requests() -> dict[str, str]:
    requests: dict[str, str] = {}
    for window in WINDOWS:
        for asset, symbol in SYMBOLS.items():
            for month in _months(window.warmup_start, window.data_end):
                requests[f"spot:{asset}:{month}"] = (
                    f"{BASE_URL}/spot/monthly/klines/{symbol}/5m/{symbol}-5m-{month}.zip"
                )
                requests[f"futures:{asset}:{month}"] = (
                    f"{BASE_URL}/futures/um/monthly/klines/{symbol}/5m/{symbol}-5m-{month}.zip"
                )
                requests[f"funding:{asset}:{month}"] = (
                    f"{BASE_URL}/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip"
                )
            for day_text in _days(window.warmup_start, window.screen_end + timedelta(hours=1)):
                requests[f"metrics:{asset}:{day_text}"] = (
                    f"{BASE_URL}/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{day_text}.zip"
                )
    return requests


def download_inputs(max_workers: int = 16) -> tuple[dict[str, DownloadedArchive], list[dict[str, str]]]:
    requests = _archive_requests()
    downloaded: dict[str, DownloadedArchive] = {}
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        pending = {executor.submit(_download, url): (key, url) for key, url in requests.items()}
        for future in as_completed(pending):
            key, url = pending[future]
            try:
                downloaded[key] = future.result()
            except HistoricalProxyScreenError as exc:
                failures.append({"key": key, "url": url, "reason": str(exc)})
    if failures:
        sample = "; ".join(item["key"] for item in sorted(failures, key=lambda item: item["key"])[:8])
        raise HistoricalDiscoveryV26Error(f"{len(failures)} required archives failed: {sample}")
    inventory = [
        {"key": key, "url": archive.url, "sha256": archive.sha256}
        for key, archive in sorted(downloaded.items())
    ]
    return downloaded, inventory


def _parse_5m_klines(archive: DownloadedArchive) -> dict[datetime, FiveMinuteBar]:
    rows = _csv_rows(archive)
    if not rows:
        raise HistoricalDiscoveryV26Error(f"Archive contains no rows: {archive.url}")
    try:
        float(rows[0][0])
    except (ValueError, IndexError):
        rows = rows[1:]
    bars: dict[datetime, FiveMinuteBar] = {}
    for index, row in enumerate(rows):
        if len(row) < 11:
            raise HistoricalDiscoveryV26Error(f"Malformed 5m kline row {index} in {archive.url}")
        timestamp = _timestamp(row[0], "kline.open_time").replace(second=0, microsecond=0)
        if timestamp.minute % 5:
            raise HistoricalDiscoveryV26Error(f"Non-5m timestamp {timestamp} in {archive.url}")
        bar = FiveMinuteBar(
            timestamp=timestamp,
            open=_finite(row[1], "kline.open", positive=True),
            high=_finite(row[2], "kline.high", positive=True),
            low=_finite(row[3], "kline.low", positive=True),
            close=_finite(row[4], "kline.close", positive=True),
            quote_volume=_finite(row[7], "kline.quote_volume"),
            taker_buy_quote_volume=_finite(row[10], "kline.taker_buy_quote_volume"),
        )
        if bar.low > bar.high or not bar.low <= bar.open <= bar.high or not bar.low <= bar.close <= bar.high:
            raise HistoricalDiscoveryV26Error(f"Invalid OHLC relationship at {timestamp}")
        prior = bars.get(timestamp)
        if prior is not None and prior != bar:
            raise HistoricalDiscoveryV26Error(f"Conflicting 5m bar {timestamp} in {archive.url}")
        bars[timestamp] = bar
    return bars


def _safe_ratio(numerator: float, denominator: float, default: float = 0.0) -> float:
    return default if denominator == 0 else numerator / denominator


def _aggregate_hour(hour: datetime, bars: list[FiveMinuteBar]) -> HourState:
    ordered = sorted(bars, key=lambda item: item.timestamp)
    expected = [hour + timedelta(minutes=5 * index) for index in range(12)]
    if [bar.timestamp for bar in ordered] != expected:
        raise HistoricalDiscoveryV26Error(f"Hour {hour} does not contain exactly twelve ordered 5m bars")
    quote_volume = sum(max(0.0, bar.quote_volume) for bar in ordered)
    taker_buy = sum(max(0.0, bar.taker_buy_quote_volume) for bar in ordered)
    imbalance = 0.0 if quote_volume <= 0 else max(-1.0, min(1.0, 2.0 * taker_buy / quote_volume - 1.0))
    path = [ordered[0].open] + [bar.close for bar in ordered]
    simple_moves = [abs(path[index] / path[index - 1] - 1.0) for index in range(1, len(path))]
    hourly_return = ordered[-1].close / ordered[0].open - 1.0
    efficiency = min(1.0, _safe_ratio(abs(hourly_return), sum(simple_moves)))
    high = max(bar.high for bar in ordered)
    low = min(bar.low for bar in ordered)
    close_location = 0.5 if high == low else (ordered[-1].close - low) / (high - low)
    max_share = _safe_ratio(max(bar.quote_volume for bar in ordered), quote_volume)
    early = sum(bar.quote_volume for bar in ordered[:3])
    late = sum(bar.quote_volume for bar in ordered[-3:])
    late_early = _safe_ratio(late, early, default=1.0 if late == 0 else 0.0)
    log_returns = [math.log(path[index] / path[index - 1]) for index in range(1, len(path))]
    realized = math.sqrt(sum(value * value for value in log_returns))
    return HourState(
        hour=hour,
        open=ordered[0].open,
        high=high,
        low=low,
        close=ordered[-1].close,
        quote_volume=quote_volume,
        taker_imbalance=imbalance,
        trend_efficiency=efficiency,
        close_location=max(0.0, min(1.0, close_location)),
        maximum_volume_share=max_share,
        late_early_volume_ratio=late_early,
        realized_volatility=realized,
        range_fraction=(high - low) / ordered[-1].close,
    )


def _hourly_states(bars: dict[datetime, FiveMinuteBar]) -> dict[datetime, HourState]:
    grouped: dict[datetime, list[FiveMinuteBar]] = {}
    for timestamp, bar in bars.items():
        hour = timestamp.replace(minute=0, second=0, microsecond=0)
        grouped.setdefault(hour, []).append(bar)
    states: dict[datetime, HourState] = {}
    for hour, hour_bars in grouped.items():
        if len(hour_bars) == 12:
            states[hour] = _aggregate_hour(hour, hour_bars)
    return states


def _latest_known(values: dict[datetime, float], hour: datetime) -> float | None:
    eligible = [timestamp for timestamp in values if timestamp <= hour + timedelta(hours=1) - timedelta(microseconds=1)]
    return None if not eligible else values[max(eligible)]


def assemble_market(downloaded: dict[str, DownloadedArchive]) -> tuple[
    dict[str, dict[datetime, HourState]],
    dict[str, dict[datetime, HourState]],
    dict[str, dict[datetime, float]],
    dict[str, dict[datetime, float]],
]:
    spot_5m: dict[str, dict[datetime, FiveMinuteBar]] = {asset: {} for asset in ASSETS}
    perp_5m: dict[str, dict[datetime, FiveMinuteBar]] = {asset: {} for asset in ASSETS}
    funding: dict[str, dict[datetime, float]] = {asset: {} for asset in ASSETS}
    oi: dict[str, dict[datetime, float]] = {asset: {} for asset in ASSETS}
    for key, archive in sorted(downloaded.items()):
        kind, asset, _period = key.split(":", 2)
        if kind in {"spot", "futures"}:
            target = spot_5m[asset] if kind == "spot" else perp_5m[asset]
            for timestamp, bar in _parse_5m_klines(archive).items():
                prior = target.get(timestamp)
                if prior is not None and prior != bar:
                    raise HistoricalDiscoveryV26Error(f"Conflicting {kind} bar {asset} {timestamp}")
                target[timestamp] = bar
        elif kind == "funding":
            funding[asset].update(_parse_funding(archive))
        elif kind == "metrics":
            oi[asset].update(_parse_metrics(archive))
    spot = {asset: _hourly_states(values) for asset, values in spot_5m.items()}
    perp = {asset: _hourly_states(values) for asset, values in perp_5m.items()}
    return spot, perp, funding, oi


def build_window_states(
    window: WindowSpec,
    spot: dict[str, dict[datetime, HourState]],
    perp: dict[str, dict[datetime, HourState]],
    funding: dict[str, dict[datetime, float]],
    oi: dict[str, dict[datetime, float]],
) -> tuple[dict[datetime, dict[str, AssetState]], list[dict[str, str]]]:
    states: dict[datetime, dict[str, AssetState]] = {}
    excluded: list[dict[str, str]] = []
    hour = window.warmup_start
    end = window.screen_end + timedelta(hours=1)
    while hour <= end:
        assets: dict[str, AssetState] = {}
        reasons: list[str] = []
        for asset in ASSETS:
            spot_state = spot[asset].get(hour)
            perp_state = perp[asset].get(hour)
            funding_value = _latest_known(funding[asset], hour)
            oi_value = oi[asset].get(hour)
            if spot_state is None:
                reasons.append(f"{asset}:missing_spot")
            if perp_state is None:
                reasons.append(f"{asset}:missing_perp")
            if funding_value is None:
                reasons.append(f"{asset}:missing_funding")
            if oi_value is None:
                reasons.append(f"{asset}:missing_oi")
            if any(value is None for value in (spot_state, perp_state, funding_value, oi_value)):
                continue
            assert spot_state is not None and perp_state is not None and funding_value is not None and oi_value is not None
            assets[asset] = AssetState(
                spot=spot_state,
                perp=perp_state,
                funding=funding_value,
                open_interest=oi_value,
                basis_bps=(perp_state.close / spot_state.close - 1.0) * 10_000.0,
                flow_lead=spot_state.taker_imbalance - perp_state.taker_imbalance,
            )
        if reasons:
            excluded.append({"hour": _utc(hour), "reason": ",".join(reasons)})
        else:
            states[hour] = assets
        hour += timedelta(hours=1)
    return states, excluded


def _change(current: float, prior: float) -> float:
    if prior <= 0:
        raise HistoricalDiscoveryV26Error("Prior value must be positive")
    return current / prior - 1.0


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = mean(left)
    right_mean = mean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True))
    left_var = sum((a - left_mean) ** 2 for a in left)
    right_var = sum((b - right_mean) ** 2 for b in right)
    denominator = math.sqrt(left_var * right_var)
    return None if denominator == 0 else numerator / denominator


def _quantile(values: Iterable[float], q: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise HistoricalDiscoveryV26Error("Quantile requires at least one value")
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(1.0, q)) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _hour_return(states: dict[datetime, dict[str, AssetState]], asset: str, hour: datetime, lag: int) -> float:
    return _change(states[hour][asset].spot.close, states[hour - timedelta(hours=lag)][asset].spot.close)


def _beta(states: dict[datetime, dict[str, AssetState]], asset: str, hour: datetime) -> float | None:
    asset_returns: list[float] = []
    btc_returns: list[float] = []
    for offset in range(168, 0, -1):
        current = hour - timedelta(hours=offset)
        prior = current - timedelta(hours=1)
        if current not in states or prior not in states:
            return None
        asset_returns.append(_change(states[current][asset].spot.close, states[prior][asset].spot.close))
        btc_returns.append(_change(states[current]["BTC"].spot.close, states[prior]["BTC"].spot.close))
    asset_mean = mean(asset_returns)
    btc_mean = mean(btc_returns)
    btc_variance = sum((value - btc_mean) ** 2 for value in btc_returns)
    if btc_variance == 0:
        return None
    covariance = sum(
        (a - asset_mean) * (b - btc_mean)
        for a, b in zip(asset_returns, btc_returns, strict=True)
    )
    return covariance / btc_variance


def _residual_return(
    states: dict[datetime, dict[str, AssetState]], asset: str, hour: datetime, lag: int, beta: float
) -> float:
    return _hour_return(states, asset, hour, lag) - beta * _hour_return(states, "BTC", hour, lag)


def _event_key(window: str, signal_hour: datetime, asset: str, family: str, values: dict[str, float]) -> str:
    payload = {
        "window": window,
        "signal_hour": _utc(signal_hour),
        "asset": asset,
        "family": family,
        "values": values,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _candidate_residual(
    window: WindowSpec,
    states: dict[datetime, dict[str, AssetState]],
    asset: str,
    signal: datetime,
    confirmation: datetime,
) -> Candidate | None:
    if asset == "BTC":
        return None
    beta = _beta(states, asset, signal)
    if beta is None:
        return None
    res6 = _residual_return(states, asset, signal, 6, beta)
    res24 = _residual_return(states, asset, signal, 24, beta)
    trailing6: list[float] = []
    trailing24: list[float] = []
    for offset in range(168, 0, -1):
        endpoint = signal - timedelta(hours=offset)
        if endpoint - timedelta(hours=24) not in states:
            return None
        trailing6.append(_residual_return(states, asset, endpoint, 6, beta))
        trailing24.append(_residual_return(states, asset, endpoint, 24, beta))
    current = states[signal][asset]
    previous = states[signal - timedelta(hours=1)][asset]
    confirm = states[confirmation][asset]
    confirm_return = _change(confirm.spot.close, current.spot.close)
    confirm_btc = _change(states[confirmation]["BTC"].spot.close, states[signal]["BTC"].spot.close)
    confirm_residual = confirm_return - beta * confirm_btc
    signal_return_1h = _change(current.spot.close, previous.spot.close)
    retracement_floor = current.spot.close - max(0.0, current.spot.close - previous.spot.close) * 0.5
    raw6 = _hour_return(states, asset, signal, 6)
    conditions = (
        res6 >= 0.012,
        res24 >= 0.020,
        res6 >= _quantile(trailing6, 0.85),
        res24 >= _quantile(trailing24, 0.85),
        0.0 < raw6 <= 0.08,
        signal_return_1h > 0.0,
        current.spot.taker_imbalance >= 0.12,
        current.flow_lead >= 0.08,
        current.spot.trend_efficiency >= 0.45,
        current.spot.close_location >= 0.60,
        abs(current.basis_bps) <= 20.0,
        current.funding <= 0.00010,
        -0.04 <= _change(current.open_interest, states[signal - timedelta(hours=6)][asset].open_interest) <= 0.06,
        confirm_return > 0.0,
        confirm_residual > 0.0,
        confirm.spot.taker_imbalance >= 0.08,
        confirm.spot.trend_efficiency >= 0.40,
        confirm.spot.close >= retracement_floor,
    )
    amplitude = res6
    if not all(conditions) or amplitude < MIN_AMPLITUDE:
        return None
    score = (
        min(4.0, res6 / 0.012)
        + min(4.0, res24 / 0.020)
        + min(2.0, current.spot.trend_efficiency / 0.45)
        + min(2.0, max(0.0, current.flow_lead) / 0.08)
        + min(2.0, max(0.0, confirm_residual) / 0.003)
    )
    values = {"res6": res6, "res24": res24, "confirm_residual": confirm_residual, "beta": beta}
    return Candidate(signal, confirmation, asset, "confirmed_residual_continuation", score, amplitude, _event_key(window.name, signal, asset, "confirmed_residual_continuation", values))


def _candidate_unwind(
    window: WindowSpec,
    states: dict[datetime, dict[str, AssetState]],
    asset: str,
    signal: datetime,
    confirmation: datetime,
) -> Candidate | None:
    current = states[signal][asset]
    prior = states[signal - timedelta(hours=1)][asset]
    six = states[signal - timedelta(hours=6)][asset]
    confirm = states[confirmation][asset]
    funding_history = [states[signal - timedelta(hours=offset)][asset].funding for offset in range(168, 0, -1)]
    basis_history = [states[signal - timedelta(hours=offset)][asset].basis_bps for offset in range(168, 0, -1)]
    oi_change = _change(current.open_interest, six.open_interest)
    basis_improvement = current.basis_bps - prior.basis_bps
    confirm_return = _change(confirm.spot.close, current.spot.close)
    confirm_basis_change = confirm.basis_bps - current.basis_bps
    raw6 = _hour_return(states, asset, signal, 6)
    conditions = (
        current.funding <= _quantile(funding_history, 0.10)
        or (current.basis_bps <= _quantile(basis_history, 0.10) and current.basis_bps < 0.0),
        oi_change <= -0.03,
        basis_improvement >= 3.0,
        current.spot.taker_imbalance >= 0.08,
        current.flow_lead >= 0.05,
        current.spot.close_location > 0.50,
        confirm_return > 0.0,
        confirm_basis_change >= 0.0,
        confirm.funding >= current.funding,
        confirm.spot.taker_imbalance >= 0.10,
        confirm.spot.trend_efficiency >= 0.35,
    )
    amplitude = abs(raw6)
    if not all(conditions) or amplitude < MIN_AMPLITUDE:
        return None
    score = (
        min(4.0, abs(oi_change) / 0.03)
        + min(4.0, basis_improvement / 3.0)
        + min(3.0, max(0.0, current.flow_lead) / 0.05)
        + min(3.0, confirm_return / 0.003)
    )
    values = {"oi_change": oi_change, "basis_improvement": basis_improvement, "confirm_return": confirm_return, "raw6": raw6}
    return Candidate(signal, confirmation, asset, "confirmed_derivatives_unwind", score, amplitude, _event_key(window.name, signal, asset, "confirmed_derivatives_unwind", values))


def _candidate_sweep(
    window: WindowSpec,
    states: dict[datetime, dict[str, AssetState]],
    asset: str,
    signal: datetime,
    confirmation: datetime,
) -> Candidate | None:
    current = states[signal][asset]
    prior = states[signal - timedelta(hours=1)][asset]
    confirm = states[confirmation][asset]
    range_history = [states[signal - timedelta(hours=offset)][asset].spot.range_fraction for offset in range(168, 0, -1)]
    volume_share_history = [states[signal - timedelta(hours=offset)][asset].spot.maximum_volume_share for offset in range(168, 0, -1)]
    current_return = _change(current.spot.close, prior.spot.close)
    confirm_return = _change(confirm.spot.close, current.spot.close)
    range_contraction = 1.0 - _safe_ratio(current.spot.range_fraction, prior.spot.range_fraction)
    conditions = (
        prior.spot.range_fraction >= _quantile(range_history, 0.85),
        prior.spot.maximum_volume_share >= _quantile(volume_share_history, 0.85),
        prior.spot.trend_efficiency >= 0.50,
        range_contraction >= 0.20,
        current.spot.late_early_volume_ratio >= 1.10,
        current.spot.taker_imbalance >= 0.12,
        current.spot.close_location >= 0.60,
        0.0 < current_return <= 0.04,
        current.flow_lead >= 0.08,
        abs(current.basis_bps) <= 20.0,
        confirm_return > 0.0,
        confirm.spot.taker_imbalance >= 0.08,
        confirm.spot.close_location >= 0.55,
        confirm.spot.range_fraction <= current.spot.range_fraction,
    )
    amplitude = prior.spot.range_fraction
    if not all(conditions) or amplitude < MIN_AMPLITUDE:
        return None
    score = (
        min(4.0, prior.spot.range_fraction / 0.010)
        + min(3.0, range_contraction / 0.20)
        + min(3.0, current.spot.late_early_volume_ratio / 1.10)
        + min(3.0, current.spot.taker_imbalance / 0.12)
        + min(3.0, confirm_return / 0.003)
    )
    values = {"prior_range": prior.spot.range_fraction, "range_contraction": range_contraction, "current_return": current_return, "confirm_return": confirm_return}
    return Candidate(signal, confirmation, asset, "intrahour_sweep_replenishment", score, amplitude, _event_key(window.name, signal, asset, "intrahour_sweep_replenishment", values))


def build_events(window: WindowSpec, states: dict[datetime, dict[str, AssetState]]) -> list[Event]:
    events: list[Event] = []
    last_event: dict[tuple[str, str], datetime] = {}
    occupied_until: datetime | None = None
    signal = window.screen_start
    while signal <= window.screen_end - timedelta(hours=1):
        confirmation = signal + timedelta(hours=1)
        required_start = signal - timedelta(hours=192)
        if any(required_start + timedelta(hours=offset) not in states for offset in range(194)):
            signal += timedelta(hours=1)
            continue
        candidates: list[Candidate] = []
        for asset in ASSETS:
            for builder in (_candidate_residual, _candidate_unwind, _candidate_sweep):
                candidate = builder(window, states, asset, signal, confirmation)
                if candidate is not None:
                    candidates.append(candidate)
        ranked = sorted(candidates, key=lambda item: (-item.score, item.asset, item.family))
        for candidate in ranked:
            prior = last_event.get((candidate.asset, candidate.family))
            entry = confirmation + timedelta(hours=1)
            if prior is not None and signal - prior < timedelta(hours=8):
                continue
            if occupied_until is not None and entry < occupied_until:
                continue
            event = Event(
                window=window.name,
                phase=window.phase,
                signal_hour=signal,
                confirmation_hour=confirmation,
                entry_hour=entry,
                asset=candidate.asset,
                family=candidate.family,
                score=candidate.score,
                amplitude=candidate.amplitude,
                weight=WEIGHT,
                event_key=candidate.event_key,
            )
            events.append(event)
            last_event[(candidate.asset, candidate.family)] = signal
            occupied_until = entry + timedelta(hours=PRIMARY_HORIZON)
            break
        signal += timedelta(hours=1)
    return events


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


def evaluate_events(
    events: list[Event],
    spot: dict[str, dict[datetime, HourState]],
    horizon: int,
    cost: float,
) -> dict[str, Any]:
    returns: list[float] = []
    gross_returns: list[float] = []
    btc_benchmark: list[float] = []
    equal_benchmark: list[float] = []
    asset_contribution: dict[str, float] = {}
    family_contribution: dict[str, float] = {}
    window_returns: dict[str, list[float]] = {window.name: [] for window in WINDOWS}
    event_rows: list[dict[str, Any]] = []
    wins = 0
    excluded: list[dict[str, str]] = []
    for event in sorted(events, key=lambda item: (item.entry_hour, item.asset, item.family)):
        exit_hour = event.entry_hour + timedelta(hours=horizon)
        entry = spot[event.asset].get(event.entry_hour)
        exit_bar = spot[event.asset].get(exit_hour)
        if entry is None or exit_bar is None:
            excluded.append({"event_key": event.event_key, "reason": "missing_entry_or_exit"})
            continue
        raw = exit_bar.open / entry.open - 1.0
        weighted_gross = event.weight * raw
        weighted_net = event.weight * (raw - cost)
        gross_returns.append(weighted_gross)
        returns.append(weighted_net)
        window_returns[event.window].append(weighted_net)
        asset_contribution[event.asset] = asset_contribution.get(event.asset, 0.0) + weighted_net
        family_contribution[event.family] = family_contribution.get(event.family, 0.0) + weighted_net
        wins += int(raw - cost > 0.0)
        btc_entry = spot["BTC"].get(event.entry_hour)
        btc_exit = spot["BTC"].get(exit_hour)
        equal_entries = [spot[asset].get(event.entry_hour) for asset in ASSETS]
        equal_exits = [spot[asset].get(exit_hour) for asset in ASSETS]
        if btc_entry is None or btc_exit is None or any(value is None for value in equal_entries + equal_exits):
            raise HistoricalDiscoveryV26Error(f"Benchmark data missing for event {event.event_key}")
        btc_benchmark.append(WEIGHT * (btc_exit.open / btc_entry.open - 1.0 - cost))
        equal_raw = sum(
            exit_state.open / entry_state.open - 1.0
            for entry_state, exit_state in zip(equal_entries, equal_exits, strict=True)
            if entry_state is not None and exit_state is not None
        ) / len(ASSETS)
        equal_benchmark.append(WEIGHT * (equal_raw - cost))
        event_rows.append({
            "event_key": event.event_key,
            "window": event.window,
            "phase": event.phase,
            "signal_hour": _utc(event.signal_hour),
            "confirmation_hour": _utc(event.confirmation_hour),
            "entry_hour": _utc(event.entry_hour),
            "exit_hour": _utc(exit_hour),
            "asset": event.asset,
            "family": event.family,
            "score": event.score,
            "amplitude": event.amplitude,
            "weight": event.weight,
            "raw_asset_return": raw,
            "portfolio_gross_return": weighted_gross,
            "portfolio_net_return": weighted_net,
        })
    positive_asset = {key: max(0.0, value) for key, value in asset_contribution.items()}
    positive_family = {key: max(0.0, value) for key, value in family_contribution.items()}
    positive_asset_total = sum(positive_asset.values())
    positive_family_total = sum(positive_family.values())
    return {
        "horizon_hours": horizon,
        "round_trip_cost": cost,
        "accepted_event_count": len(event_rows),
        "event_win_rate": 0.0 if not event_rows else wins / len(event_rows),
        "gross_compounded_return": _compounded(gross_returns),
        "net_compounded_return": _compounded(returns),
        "maximum_drawdown": _maximum_drawdown(returns),
        "btc_15pct_benchmark_return": _compounded(btc_benchmark),
        "equal_weight_15pct_benchmark_return": _compounded(equal_benchmark),
        "window_returns": {name: _compounded(values) for name, values in window_returns.items()},
        "asset_net_contribution": dict(sorted(asset_contribution.items())),
        "family_net_contribution": dict(sorted(family_contribution.items())),
        "maximum_positive_asset_share": 0.0 if positive_asset_total <= 0 else max(positive_asset.values(), default=0.0) / positive_asset_total,
        "maximum_positive_family_share": 0.0 if positive_family_total <= 0 else max(positive_family.values(), default=0.0) / positive_family_total,
        "events": event_rows,
        "excluded_events": excluded,
    }


def run_discovery(max_workers: int = 16) -> dict[str, Any]:
    if not PROTOCOL_PATH.is_file():
        raise HistoricalDiscoveryV26Error(f"Protocol is missing: {PROTOCOL_PATH}")
    downloaded, source_inventory = download_inputs(max_workers=max_workers)
    spot, perp, funding, oi = assemble_market(downloaded)
    all_events: list[Event] = []
    excluded_by_window: dict[str, list[dict[str, str]]] = {}
    for window in WINDOWS:
        states, excluded = build_window_states(window, spot, perp, funding, oi)
        excluded_by_window[window.name] = excluded
        if excluded:
            continue
        all_events.extend(build_events(window, states))
    results = {
        str(horizon): {
            "standard": evaluate_events(all_events, spot, horizon, STANDARD_COST),
            "stress": evaluate_events(all_events, spot, horizon, STRESS_COST),
        }
        for horizon in HORIZONS
    }
    primary = results["4"]["standard"]
    stress = results["4"]["stress"]
    active_assets = sorted({event.asset for event in all_events})
    active_families = sorted({event.family for event in all_events})
    active_days = sorted({event.signal_hour.date().isoformat() for event in all_events})
    discovery_return = _compounded([
        primary["window_returns"]["2025-08"],
        primary["window_returns"]["2025-11"],
    ])
    validation_positive = all(primary["window_returns"][name] > 0.0 for name in ("2026-02", "2026-05"))
    gates = {
        "positive_standard_return": primary["net_compounded_return"] > 0.0,
        "non_negative_stress_return": stress["net_compounded_return"] >= 0.0,
        "positive_discovery": discovery_return > 0.0,
        "positive_each_validation_window": validation_positive,
        "minimum_events": primary["accepted_event_count"] >= 30,
        "minimum_active_days": len(active_days) >= 20,
        "minimum_active_families": len(active_families) >= 2,
        "minimum_active_assets": len(active_assets) >= 4,
        "drawdown_cap": primary["maximum_drawdown"] <= 0.08,
        "beat_cash": primary["net_compounded_return"] > 0.0,
        "beat_btc_benchmark": primary["net_compounded_return"] > primary["btc_15pct_benchmark_return"],
        "beat_equal_weight_benchmark": primary["net_compounded_return"] > primary["equal_weight_15pct_benchmark_return"],
        "positive_sensitivity": results["2"]["standard"]["net_compounded_return"] > 0.0 or results["8"]["standard"]["net_compounded_return"] > 0.0,
        "asset_concentration": primary["maximum_positive_asset_share"] <= 0.50,
        "family_concentration": primary["maximum_positive_family_share"] <= 0.65,
        "all_windows_complete": all(not values for values in excluded_by_window.values()),
    }
    source_path = Path(__file__).resolve()
    report: dict[str, Any] = {
        "schema_version": "2.6-cost-aware-historical-discovery",
        "mode": MODE,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "changes_track_a": False,
        "cannot_replace_forward_evidence": True,
        "windows": [
            {
                "name": window.name,
                "phase": window.phase,
                "warmup_start_utc": _utc(window.warmup_start),
                "screen_start_utc": _utc(window.screen_start),
                "screen_end_utc": _utc(window.screen_end),
            }
            for window in WINDOWS
        ],
        "source_inventory": source_inventory,
        "source_inventory_sha256": hashlib.sha256(canonical_json(source_inventory).encode("utf-8")).hexdigest(),
        "excluded_hours_by_window": excluded_by_window,
        "event_count": len(all_events),
        "active_days_utc": active_days,
        "active_assets": active_assets,
        "active_families": active_families,
        "results": results,
        "screening_gates": gates,
        "screening_status": "ENCOURAGING_HISTORICAL_DISCOVERY" if all(gates.values()) else "NOT_ENCOURAGING_HISTORICAL_DISCOVERY",
        "fingerprints": {
            "implementation_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "protocol_sha256": hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest(),
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
    parser = argparse.ArgumentParser(description="Run isolated v2.6 cost-aware historical discovery.")
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--max-workers", type=int, default=16)
    args = parser.parse_args(argv)
    report = run_discovery(max_workers=args.max_workers)
    _atomic_json(Path(args.json_out), report)
    primary = report["results"]["4"]["standard"]
    print(json.dumps({
        "status": report["screening_status"],
        "events": report["event_count"],
        "active_assets": report["active_assets"],
        "active_families": report["active_families"],
        "four_hour_net_return": primary["net_compounded_return"],
        "four_hour_stress_return": report["results"]["4"]["stress"]["net_compounded_return"],
        "maximum_drawdown": primary["maximum_drawdown"],
        "report_sha256": report["report_sha256"],
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
