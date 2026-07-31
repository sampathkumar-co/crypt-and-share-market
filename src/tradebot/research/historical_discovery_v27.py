from __future__ import annotations

import argparse
import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import historical_discovery_v26 as v26
from tradebot.research.historical_proxy_screen_v25 import (
    BASE_URL,
    DownloadedArchive,
    HistoricalProxyScreenError,
    _download,
)

MODE = "HISTORICAL_FIVEFOLD_MECHANISM_DISCOVERY_ONLY"
PROTOCOL_PATH = Path("research/V27_FIVEFOLD_MECHANISM_DISCOVERY_PROTOCOL.md")
ASSETS = v26.ASSETS
SYMBOLS = v26.SYMBOLS
HORIZONS = (4, 8, 12)
PRIMARY_HORIZON = 8
STANDARD_COST = 0.002
STRESS_COST = 0.004
WEIGHT = 0.15
WARMUP_HOURS = 10 * 24
COOLDOWN_HOURS = 12

WindowSpec = v26.WindowSpec
WINDOWS = (
    WindowSpec("2024-07", "discovery", datetime(2024, 7, 1, tzinfo=timezone.utc), datetime(2024, 7, 28, 23, tzinfo=timezone.utc)),
    WindowSpec("2024-12", "discovery", datetime(2024, 12, 1, tzinfo=timezone.utc), datetime(2024, 12, 28, 23, tzinfo=timezone.utc)),
    WindowSpec("2025-03", "validation", datetime(2025, 3, 1, tzinfo=timezone.utc), datetime(2025, 3, 28, 23, tzinfo=timezone.utc)),
    WindowSpec("2025-06", "validation", datetime(2025, 6, 1, tzinfo=timezone.utc), datetime(2025, 6, 28, 23, tzinfo=timezone.utc)),
    WindowSpec("2025-09", "validation", datetime(2025, 9, 1, tzinfo=timezone.utc), datetime(2025, 9, 28, 23, tzinfo=timezone.utc)),
    WindowSpec("2025-12", "validation", datetime(2025, 12, 1, tzinfo=timezone.utc), datetime(2025, 12, 28, 23, tzinfo=timezone.utc)),
    WindowSpec("2026-04", "validation", datetime(2026, 4, 1, tzinfo=timezone.utc), datetime(2026, 4, 28, 23, tzinfo=timezone.utc)),
)
VALIDATION_WINDOWS = tuple(window.name for window in WINDOWS if window.phase == "validation")
DISCOVERY_WINDOWS = tuple(window.name for window in WINDOWS if window.phase == "discovery")


class HistoricalDiscoveryV27Error(RuntimeError):
    """Raised when the v2.7 fivefold screen cannot be reproduced safely."""


@dataclass(frozen=True)
class Candidate:
    signal_hour: datetime
    confirmation_hour: datetime
    asset: str
    family: str
    score: float
    amplitude: float
    diagnostics: dict[str, float]
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
    diagnostics: dict[str, float]
    weight: float
    event_key: str


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _archive_requests() -> dict[str, str]:
    requests: dict[str, str] = {}
    for window in WINDOWS:
        warmup_start = window.screen_start - timedelta(hours=WARMUP_HOURS)
        data_end = window.screen_end + timedelta(hours=14)
        for asset, symbol in SYMBOLS.items():
            for month in v26._months(warmup_start, data_end):
                requests[f"spot:{asset}:{month}"] = (
                    f"{BASE_URL}/spot/monthly/klines/{symbol}/5m/{symbol}-5m-{month}.zip"
                )
                requests[f"futures:{asset}:{month}"] = (
                    f"{BASE_URL}/futures/um/monthly/klines/{symbol}/5m/{symbol}-5m-{month}.zip"
                )
                requests[f"funding:{asset}:{month}"] = (
                    f"{BASE_URL}/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip"
                )
            for day_text in v26._days(warmup_start, window.screen_end + timedelta(hours=1)):
                requests[f"metrics:{asset}:{day_text}"] = (
                    f"{BASE_URL}/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{day_text}.zip"
                )
    return requests


def download_inputs(max_workers: int = 20) -> tuple[dict[str, DownloadedArchive], list[dict[str, str]]]:
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
        sample = "; ".join(
            item["key"] for item in sorted(failures, key=lambda item: item["key"])[:10]
        )
        raise HistoricalDiscoveryV27Error(
            f"{len(failures)} required archives failed: {sample}"
        )
    inventory = [
        {"key": key, "url": archive.url, "sha256": archive.sha256}
        for key, archive in sorted(downloaded.items())
    ]
    return downloaded, inventory


def _change(current: float, prior: float) -> float:
    return v26._change(current, prior)


def _ret(
    states: dict[datetime, dict[str, v26.AssetState]],
    asset: str,
    hour: datetime,
    lag: int,
) -> float:
    return _change(
        states[hour][asset].spot.close,
        states[hour - timedelta(hours=lag)][asset].spot.close,
    )


def _oi_change(
    states: dict[datetime, dict[str, v26.AssetState]],
    asset: str,
    hour: datetime,
    lag: int,
) -> float:
    return _change(
        states[hour][asset].open_interest,
        states[hour - timedelta(hours=lag)][asset].open_interest,
    )


def _history_values(
    states: dict[datetime, dict[str, v26.AssetState]],
    asset: str,
    hour: datetime,
    field: str,
) -> list[float]:
    values: list[float] = []
    for offset in range(168, 0, -1):
        state = states[hour - timedelta(hours=offset)][asset]
        if field == "funding":
            values.append(state.funding)
        elif field == "basis":
            values.append(state.basis_bps)
        elif field == "range":
            values.append(state.spot.range_fraction)
        elif field == "volatility":
            values.append(state.spot.realized_volatility)
        elif field == "volume":
            values.append(state.spot.quote_volume)
        else:
            raise HistoricalDiscoveryV27Error(f"Unknown history field: {field}")
    return values


def _event_key(
    window: str,
    signal_hour: datetime,
    asset: str,
    family: str,
    diagnostics: dict[str, float],
) -> str:
    payload = {
        "schema_version": "2.7-fivefold-mechanism-discovery",
        "window": window,
        "signal_hour": _utc(signal_hour),
        "asset": asset,
        "family": family,
        "diagnostics": diagnostics,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _candidate_pullback(
    window: WindowSpec,
    states: dict[datetime, dict[str, v26.AssetState]],
    asset: str,
    signal: datetime,
    confirmation: datetime,
) -> Candidate | None:
    if asset == "BTC":
        return None
    beta = v26._beta(states, asset, signal)
    if beta is None:
        return None
    current = states[signal][asset]
    confirm = states[confirmation][asset]
    prior = states[signal - timedelta(hours=1)][asset]
    asset72 = _ret(states, asset, signal, 72)
    btc72 = _ret(states, "BTC", signal, 72)
    residual72 = asset72 - beta * btc72
    residual3 = _ret(states, asset, signal, 3) - beta * _ret(states, "BTC", signal, 3)
    signal1 = _change(current.spot.close, prior.spot.close)
    oi24 = _oi_change(states, asset, signal, 24)
    confirm_return = _change(confirm.spot.close, current.spot.close)
    confirm_btc = _change(
        states[confirmation]["BTC"].spot.close,
        states[signal]["BTC"].spot.close,
    )
    confirm_residual = confirm_return - beta * confirm_btc
    reclaim = current.spot.low + 0.50 * (current.spot.high - current.spot.low)
    conditions = (
        asset72 >= 0.035,
        btc72 >= 0.005,
        residual72 >= 0.015,
        -0.030 <= residual3 <= -0.005,
        -0.025 <= signal1 < 0.0,
        current.funding <= 0.00015,
        abs(current.basis_bps) <= 30.0,
        -0.10 <= oi24 <= 0.12,
        current.flow_lead >= -0.05,
        confirm_return >= 0.0025,
        confirm_residual > 0.0,
        confirm.spot.close >= reclaim,
        confirm.spot.taker_imbalance >= 0.06,
        confirm.flow_lead >= 0.02,
        confirm.spot.close_location >= 0.58,
        confirm.spot.trend_efficiency >= 0.30,
    )
    if not all(conditions):
        return None
    amplitude = abs(residual3)
    score = (
        min(4.0, residual72 / 0.015)
        + min(3.0, amplitude / 0.005)
        + min(3.0, confirm_return / 0.0025)
        + min(2.0, max(0.0, confirm.flow_lead) / 0.02)
        + min(2.0, confirm.spot.close_location / 0.58)
    )
    diagnostics = {
        "beta": beta,
        "asset72": asset72,
        "btc72": btc72,
        "residual72": residual72,
        "residual3": residual3,
        "signal1": signal1,
        "oi24": oi24,
        "confirm_return": confirm_return,
        "confirm_residual": confirm_residual,
    }
    family = "trend_pullback_reclaim"
    return Candidate(
        signal,
        confirmation,
        asset,
        family,
        score,
        amplitude,
        diagnostics,
        _event_key(window.name, signal, asset, family, diagnostics),
    )


def _candidate_capitulation(
    window: WindowSpec,
    states: dict[datetime, dict[str, v26.AssetState]],
    asset: str,
    signal: datetime,
    confirmation: datetime,
) -> Candidate | None:
    current = states[signal][asset]
    confirm = states[confirmation][asset]
    beta = 1.0 if asset == "BTC" else v26._beta(states, asset, signal)
    if beta is None:
        return None
    asset6 = _ret(states, asset, signal, 6)
    residual6 = asset6 - beta * _ret(states, "BTC", signal, 6)
    oi6 = _oi_change(states, asset, signal, 6)
    funding_history = _history_values(states, asset, signal, "funding")
    basis_history = _history_values(states, asset, signal, "basis")
    funding_floor = v26._quantile(funding_history, 0.12)
    basis_floor = v26._quantile(basis_history, 0.12)
    confirm_return = _change(confirm.spot.close, current.spot.close)
    basis_improvement = confirm.basis_bps - current.basis_bps
    confirm_oi = _change(confirm.open_interest, current.open_interest)
    pressure = (
        current.funding <= funding_floor
        or (current.basis_bps <= basis_floor and current.basis_bps < -5.0)
    )
    conditions = (
        asset6 <= -0.035 or residual6 <= -0.025,
        oi6 <= -0.035,
        pressure,
        current.spot.close_location <= 0.45,
        current.spot.taker_imbalance <= -0.05,
        confirm_return >= 0.004,
        confirm.spot.close_location >= 0.62,
        confirm.spot.taker_imbalance >= 0.08,
        confirm.flow_lead >= 0.03,
        basis_improvement >= 2.0,
        confirm_oi >= -0.012,
        confirm.spot.trend_efficiency >= 0.32,
    )
    if not all(conditions):
        return None
    amplitude = max(abs(min(0.0, asset6)), abs(min(0.0, residual6)))
    score = (
        min(4.0, amplitude / 0.025)
        + min(4.0, abs(oi6) / 0.035)
        + min(3.0, confirm_return / 0.004)
        + min(2.0, basis_improvement / 2.0)
        + min(2.0, max(0.0, confirm.flow_lead) / 0.03)
    )
    diagnostics = {
        "beta": beta,
        "asset6": asset6,
        "residual6": residual6,
        "oi6": oi6,
        "funding_floor": funding_floor,
        "basis_floor": basis_floor,
        "confirm_return": confirm_return,
        "basis_improvement": basis_improvement,
        "confirm_oi": confirm_oi,
    }
    family = "post_capitulation_recovery"
    return Candidate(
        signal,
        confirmation,
        asset,
        family,
        score,
        amplitude,
        diagnostics,
        _event_key(window.name, signal, asset, family, diagnostics),
    )


def _candidate_breakout(
    window: WindowSpec,
    states: dict[datetime, dict[str, v26.AssetState]],
    asset: str,
    signal: datetime,
    confirmation: datetime,
) -> Candidate | None:
    current = states[signal][asset]
    confirm = states[confirmation][asset]
    compression = [
        states[signal - timedelta(hours=offset)][asset].spot
        for offset in range(12, 0, -1)
    ]
    breakout_level = max(item.high for item in compression)
    compression_vol = mean(item.realized_volatility for item in compression)
    compression_range = mean(item.range_fraction for item in compression)
    vol_history = _history_values(states, asset, signal, "volatility")
    range_history = _history_values(states, asset, signal, "range")
    volume_history = _history_values(states, asset, signal, "volume")[-48:]
    vol_threshold = v26._quantile(vol_history, 0.30)
    range_threshold = v26._quantile(range_history, 0.30)
    volume_ratio = current.spot.quote_volume / max(median(volume_history), 1e-12)
    breakout_return = current.spot.close / breakout_level - 1.0
    signal_return = _change(
        current.spot.close,
        states[signal - timedelta(hours=1)][asset].spot.close,
    )
    btc48 = _ret(states, "BTC", signal, 48)
    confirm_return = _change(confirm.spot.close, current.spot.close)
    hold_fraction = confirm.spot.close / breakout_level - 1.0
    conditions = (
        compression_vol <= vol_threshold,
        compression_range <= range_threshold,
        btc48 >= -0.01,
        breakout_return >= 0.001,
        0.0 < signal_return <= 0.045,
        volume_ratio >= 1.35,
        current.spot.taker_imbalance >= 0.12,
        current.flow_lead >= 0.05,
        current.spot.trend_efficiency >= 0.48,
        current.spot.close_location >= 0.68,
        abs(current.basis_bps) <= 25.0,
        current.funding <= 0.00015,
        hold_fraction >= 0.0,
        confirm_return >= -0.004,
        confirm.spot.taker_imbalance >= 0.04,
        confirm.spot.close_location >= 0.52,
        confirm.flow_lead >= -0.02,
    )
    if not all(conditions):
        return None
    amplitude = breakout_return
    score = (
        min(4.0, volume_ratio / 1.35)
        + min(3.0, breakout_return / 0.001)
        + min(3.0, current.spot.trend_efficiency / 0.48)
        + min(2.0, max(0.0, current.flow_lead) / 0.05)
        + min(2.0, max(0.0, hold_fraction) / 0.001)
    )
    diagnostics = {
        "compression_vol": compression_vol,
        "vol_threshold": vol_threshold,
        "compression_range": compression_range,
        "range_threshold": range_threshold,
        "volume_ratio": volume_ratio,
        "breakout_level": breakout_level,
        "breakout_return": breakout_return,
        "signal_return": signal_return,
        "btc48": btc48,
        "confirm_return": confirm_return,
        "hold_fraction": hold_fraction,
    }
    family = "compression_breakout_hold"
    return Candidate(
        signal,
        confirmation,
        asset,
        family,
        score,
        amplitude,
        diagnostics,
        _event_key(window.name, signal, asset, family, diagnostics),
    )


BUILDERS = (_candidate_pullback, _candidate_capitulation, _candidate_breakout)


def build_events(
    window: WindowSpec,
    states: dict[datetime, dict[str, v26.AssetState]],
) -> list[Event]:
    events: list[Event] = []
    last_event: dict[tuple[str, str], datetime] = {}
    occupied_until: datetime | None = None
    signal = window.screen_start
    while signal <= window.screen_end - timedelta(hours=1):
        confirmation = signal + timedelta(hours=1)
        required_start = signal - timedelta(hours=168)
        if any(
            required_start + timedelta(hours=offset) not in states
            for offset in range(170)
        ):
            signal += timedelta(hours=1)
            continue
        candidates: list[Candidate] = []
        for asset in ASSETS:
            for builder in BUILDERS:
                candidate = builder(window, states, asset, signal, confirmation)
                if candidate is not None:
                    candidates.append(candidate)
        ranked = sorted(
            candidates,
            key=lambda item: (-item.score, item.asset, item.family),
        )
        for candidate in ranked:
            prior = last_event.get((candidate.asset, candidate.family))
            entry = confirmation + timedelta(hours=1)
            if prior is not None and signal - prior < timedelta(hours=COOLDOWN_HOURS):
                continue
            if occupied_until is not None and entry < occupied_until:
                continue
            events.append(
                Event(
                    window=window.name,
                    phase=window.phase,
                    signal_hour=signal,
                    confirmation_hour=confirmation,
                    entry_hour=entry,
                    asset=candidate.asset,
                    family=candidate.family,
                    score=candidate.score,
                    amplitude=candidate.amplitude,
                    diagnostics=candidate.diagnostics,
                    weight=WEIGHT,
                    event_key=candidate.event_key,
                )
            )
            last_event[(candidate.asset, candidate.family)] = signal
            occupied_until = entry + timedelta(hours=PRIMARY_HORIZON)
            break
        signal += timedelta(hours=1)
    return events


def _compounded(values: list[float]) -> float:
    return v26._compounded(values)


def _maximum_drawdown(values: list[float]) -> float:
    return v26._maximum_drawdown(values)


def evaluate_events(
    events: list[Event],
    spot: dict[str, dict[datetime, v26.HourState]],
    horizon: int,
    cost: float,
) -> dict[str, Any]:
    returns: list[float] = []
    gross_returns: list[float] = []
    btc_returns: list[float] = []
    equal_returns: list[float] = []
    window_values: dict[str, list[float]] = {window.name: [] for window in WINDOWS}
    asset_contribution: dict[str, float] = {}
    family_contribution: dict[str, float] = {}
    validation_asset: dict[str, float] = {}
    validation_family: dict[str, float] = {}
    rows: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    wins = 0
    for event in sorted(events, key=lambda item: (item.entry_hour, item.asset, item.family)):
        exit_hour = event.entry_hour + timedelta(hours=horizon)
        entry = spot[event.asset].get(event.entry_hour)
        exit_state = spot[event.asset].get(exit_hour)
        if entry is None or exit_state is None:
            excluded.append({"event_key": event.event_key, "reason": "missing_entry_or_exit"})
            continue
        raw = exit_state.open / entry.open - 1.0
        gross = event.weight * raw
        net = event.weight * (raw - cost)
        gross_returns.append(gross)
        returns.append(net)
        window_values[event.window].append(net)
        asset_contribution[event.asset] = asset_contribution.get(event.asset, 0.0) + net
        family_contribution[event.family] = family_contribution.get(event.family, 0.0) + net
        if event.phase == "validation":
            validation_asset[event.asset] = validation_asset.get(event.asset, 0.0) + net
            validation_family[event.family] = validation_family.get(event.family, 0.0) + net
        wins += int(raw > cost)
        btc_entry = spot["BTC"].get(event.entry_hour)
        btc_exit = spot["BTC"].get(exit_hour)
        equal_entries = [spot[asset].get(event.entry_hour) for asset in ASSETS]
        equal_exits = [spot[asset].get(exit_hour) for asset in ASSETS]
        if btc_entry is None or btc_exit is None or any(
            value is None for value in equal_entries + equal_exits
        ):
            raise HistoricalDiscoveryV27Error(
                f"Benchmark data missing for {event.event_key}"
            )
        btc_returns.append(
            WEIGHT * (btc_exit.open / btc_entry.open - 1.0 - cost)
        )
        equal_raw = sum(
            exit_item.open / entry_item.open - 1.0
            for entry_item, exit_item in zip(equal_entries, equal_exits, strict=True)
            if entry_item is not None and exit_item is not None
        ) / len(ASSETS)
        equal_returns.append(WEIGHT * (equal_raw - cost))
        rows.append(
            {
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
                "diagnostics": event.diagnostics,
                "weight": event.weight,
                "raw_asset_return": raw,
                "portfolio_gross_return": gross,
                "portfolio_net_return": net,
            }
        )

    def positive_share(values: dict[str, float]) -> float:
        positive = [max(0.0, value) for value in values.values()]
        total = sum(positive)
        return 0.0 if total <= 0 else max(positive, default=0.0) / total

    return {
        "horizon_hours": horizon,
        "round_trip_cost": cost,
        "accepted_event_count": len(rows),
        "event_win_rate": 0.0 if not rows else wins / len(rows),
        "gross_compounded_return": _compounded(gross_returns),
        "net_compounded_return": _compounded(returns),
        "maximum_drawdown": _maximum_drawdown(returns),
        "btc_15pct_benchmark_return": _compounded(btc_returns),
        "equal_weight_15pct_benchmark_return": _compounded(equal_returns),
        "window_returns": {
            name: _compounded(values) for name, values in window_values.items()
        },
        "window_event_counts": {
            name: sum(1 for row in rows if row["window"] == name)
            for name in window_values
        },
        "asset_net_contribution": dict(sorted(asset_contribution.items())),
        "family_net_contribution": dict(sorted(family_contribution.items())),
        "validation_asset_net_contribution": dict(sorted(validation_asset.items())),
        "validation_family_net_contribution": dict(sorted(validation_family.items())),
        "maximum_positive_validation_asset_share": positive_share(validation_asset),
        "maximum_positive_validation_family_share": positive_share(validation_family),
        "events": rows,
        "excluded_events": excluded,
    }


def run_discovery(max_workers: int = 20) -> dict[str, Any]:
    if not PROTOCOL_PATH.is_file():
        raise HistoricalDiscoveryV27Error(f"Protocol is missing: {PROTOCOL_PATH}")
    downloaded, source_inventory = download_inputs(max_workers=max_workers)
    spot, perp, funding, oi = v26.assemble_market(downloaded)
    events: list[Event] = []
    excluded_by_window: dict[str, list[dict[str, str]]] = {}
    for window in WINDOWS:
        states, excluded = v26.build_window_states(
            window,
            spot,
            perp,
            funding,
            oi,
        )
        excluded_by_window[window.name] = excluded
        if not excluded:
            events.extend(build_events(window, states))
    results = {
        str(horizon): {
            "standard": evaluate_events(events, spot, horizon, STANDARD_COST),
            "stress": evaluate_events(events, spot, horizon, STRESS_COST),
        }
        for horizon in HORIZONS
    }
    primary = results[str(PRIMARY_HORIZON)]["standard"]
    stress = results[str(PRIMARY_HORIZON)]["stress"]
    validation_events = [
        event for event in events if event.phase == "validation"
    ]
    validation_assets = sorted({event.asset for event in validation_events})
    validation_families = sorted({event.family for event in validation_events})
    discovery_positive = all(
        primary["window_returns"][name] > 0.0 for name in DISCOVERY_WINDOWS
    )
    validation_positive = all(
        primary["window_returns"][name] > 0.0 for name in VALIDATION_WINDOWS
    )
    validation_stress_nonnegative_count = sum(
        stress["window_returns"][name] >= 0.0 for name in VALIDATION_WINDOWS
    )
    minimum_validation_events = all(
        primary["window_event_counts"][name] >= 6 for name in VALIDATION_WINDOWS
    )
    validation_event_count = sum(
        primary["window_event_counts"][name] for name in VALIDATION_WINDOWS
    )
    gates = {
        "positive_standard_return": primary["net_compounded_return"] > 0.0,
        "non_negative_stress_return": stress["net_compounded_return"] >= 0.0,
        "positive_each_discovery_window": discovery_positive,
        "positive_five_validation_windows": validation_positive,
        "stress_nonnegative_at_least_four_validations": validation_stress_nonnegative_count >= 4,
        "minimum_six_events_each_validation": minimum_validation_events,
        "minimum_45_validation_events": validation_event_count >= 45,
        "minimum_70_total_events": primary["accepted_event_count"] >= 70,
        "minimum_two_validation_families": len(validation_families) >= 2,
        "minimum_four_validation_assets": len(validation_assets) >= 4,
        "drawdown_cap": primary["maximum_drawdown"] <= 0.06,
        "beat_cash": primary["net_compounded_return"] > 0.0,
        "beat_btc_benchmark": primary["net_compounded_return"] > primary["btc_15pct_benchmark_return"],
        "beat_equal_weight_benchmark": primary["net_compounded_return"] > primary["equal_weight_15pct_benchmark_return"],
        "positive_sensitivity": (
            results["4"]["standard"]["net_compounded_return"] > 0.0
            or results["12"]["standard"]["net_compounded_return"] > 0.0
        ),
        "validation_asset_concentration": primary["maximum_positive_validation_asset_share"] <= 0.45,
        "validation_family_concentration": primary["maximum_positive_validation_family_share"] <= 0.70,
        "all_windows_complete": all(not values for values in excluded_by_window.values()),
    }
    active_assets = sorted({event.asset for event in events})
    active_families = sorted({event.family for event in events})
    active_days = sorted({event.signal_hour.date().isoformat() for event in events})
    source_path = Path(__file__).resolve()
    report: dict[str, Any] = {
        "schema_version": "2.7-fivefold-mechanism-discovery",
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
                "warmup_start_utc": _utc(window.screen_start - timedelta(hours=WARMUP_HOURS)),
                "screen_start_utc": _utc(window.screen_start),
                "screen_end_utc": _utc(window.screen_end),
            }
            for window in WINDOWS
        ],
        "source_inventory": source_inventory,
        "source_inventory_sha256": hashlib.sha256(
            canonical_json(source_inventory).encode("utf-8")
        ).hexdigest(),
        "excluded_hours_by_window": excluded_by_window,
        "event_count": len(events),
        "validation_event_count": validation_event_count,
        "validation_stress_nonnegative_count": validation_stress_nonnegative_count,
        "active_days_utc": active_days,
        "active_assets": active_assets,
        "active_families": active_families,
        "validation_assets": validation_assets,
        "validation_families": validation_families,
        "results": results,
        "screening_gates": gates,
        "screening_status": (
            "FIVEFOLD_HISTORICAL_BREAKTHROUGH_CANDIDATE"
            if all(gates.values())
            else "NOT_FIVEFOLD_VERIFIED"
        ),
        "fingerprints": {
            "implementation_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "protocol_sha256": hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest(),
        },
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
        description="Run isolated v2.7 fivefold mechanism discovery."
    )
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--max-workers", type=int, default=20)
    args = parser.parse_args(argv)
    report = run_discovery(max_workers=args.max_workers)
    _atomic_json(Path(args.json_out), report)
    primary = report["results"]["8"]["standard"]
    print(
        json.dumps(
            {
                "status": report["screening_status"],
                "events": report["event_count"],
                "validation_events": report["validation_event_count"],
                "active_assets": report["active_assets"],
                "active_families": report["active_families"],
                "validation_windows": {
                    name: primary["window_returns"][name]
                    for name in VALIDATION_WINDOWS
                },
                "eight_hour_net_return": primary["net_compounded_return"],
                "eight_hour_stress_return": report["results"]["8"]["stress"]["net_compounded_return"],
                "maximum_drawdown": primary["maximum_drawdown"],
                "report_sha256": report["report_sha256"],
                "paper_only": True,
                "authorizes_trading": False,
                "authorizes_shadow_paper": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
