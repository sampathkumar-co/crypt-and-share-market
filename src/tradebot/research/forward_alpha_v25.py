from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tradebot.research import market_state_router as v21


SCHEMA_VERSION = "2.5"
ASSETS = v21.ASSETS
MIN_PRIOR_HOURS = 168
PROTOCOL_PATH = Path("research/V25_HIGH_CONVICTION_ALPHA_PROTOCOL.md")
STANDARD_ROUND_TRIP_COST = 0.002


class ForwardAlphaV25Error(RuntimeError):
    """Raised when v2.5 forward evidence violates the frozen contract."""


@dataclass(frozen=True)
class HighConvictionCandidate:
    asset: str
    family: str
    score: float
    amplitude: float
    conditions: dict[str, bool]
    features: dict[str, float]
    event_key: str


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _hour(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ForwardAlphaV25Error(f"{field} is not numeric") from exc
    if not math.isfinite(result):
        raise ForwardAlphaV25Error(f"{field} is not finite")
    return result


def _percentile(values: list[float], probability: float) -> float:
    if not values:
        raise ForwardAlphaV25Error("Cannot calculate percentile from empty sample")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _clip(value: float, lower: float = 0.0, upper: float = 3.0) -> float:
    return max(lower, min(upper, value))


def _conditions(**items: bool) -> dict[str, bool]:
    return {name: bool(value) for name, value in items.items()}


def _available(record: dict[str, Any], family: str) -> dict[str, Any] | None:
    value = record.get(family)
    return value if isinstance(value, dict) and value.get("available") is True else None


def _asset_values(frame: v21.SnapshotFrame, asset: str) -> dict[str, float] | None:
    record = frame.assets.get(asset)
    if not isinstance(record, dict):
        return None
    quote = _available(record, "spot_quote")
    book = _available(record, "spot_book")
    spot_flow = _available(record, "spot_trade_flow")
    perp = _available(record, "perp_state")
    perp_flow = _available(record, "perp_trade_flow")
    cross = _available(record, "cross_venue")
    if any(item is None for item in (quote, book, spot_flow, perp, perp_flow, cross)):
        return None
    assert quote is not None and book is not None and spot_flow is not None
    assert perp is not None and perp_flow is not None and cross is not None
    bid_notional = _number(book.get("bid_notional"), f"{asset}.bid_notional")
    ask_notional = _number(book.get("ask_notional"), f"{asset}.ask_notional")
    return {
        "spot_mid": _number(quote.get("mid"), f"{asset}.spot_mid"),
        "spot_spread_bps": _number(book.get("spread_bps"), f"{asset}.spot_spread_bps"),
        "spot_book_notional": bid_notional + ask_notional,
        "spot_book_imbalance": _number(book.get("imbalance"), f"{asset}.spot_book_imbalance"),
        "spot_taker_imbalance": _number(spot_flow.get("taker_imbalance"), f"{asset}.spot_taker_imbalance"),
        "funding": _number(perp.get("funding"), f"{asset}.funding"),
        "open_interest_base": _number(perp.get("open_interest_base"), f"{asset}.open_interest_base"),
        "perp_flow_imbalance": _number(perp_flow.get("reported_side_imbalance"), f"{asset}.perp_flow_imbalance"),
        "basis_bps": _number(cross.get("spot_perp_basis_bps"), f"{asset}.basis_bps"),
    }


def _change(current: float, prior: float, field: str) -> float:
    if prior <= 0:
        raise ForwardAlphaV25Error(f"{field} prior value must be positive")
    return current / prior - 1.0


def _hourly_returns(frames: list[v21.SnapshotFrame], asset: str) -> list[float]:
    prices: list[float] = []
    for frame in frames:
        record = _asset_values(frame, asset)
        if record is None:
            return []
        prices.append(record["spot_mid"])
    return [
        _change(prices[index], prices[index - 1], f"{asset}.hourly_return")
        for index in range(1, len(prices))
    ]


def _beta(asset_returns: list[float], btc_returns: list[float]) -> float:
    if len(asset_returns) != len(btc_returns) or len(asset_returns) < 24:
        raise ForwardAlphaV25Error("Beta requires aligned return histories")
    btc_mean = sum(btc_returns) / len(btc_returns)
    asset_mean = sum(asset_returns) / len(asset_returns)
    variance = sum((value - btc_mean) ** 2 for value in btc_returns)
    if variance <= 1e-18:
        return 0.0
    covariance = sum(
        (btc - btc_mean) * (asset - asset_mean)
        for asset, btc in zip(asset_returns, btc_returns, strict=True)
    )
    return covariance / variance


def _rolling_residuals(
    frames: list[v21.SnapshotFrame],
    asset: str,
    beta: float,
    horizon: int,
) -> list[float]:
    asset_prices: list[float] = []
    btc_prices: list[float] = []
    for frame in frames:
        asset_record = _asset_values(frame, asset)
        btc_record = _asset_values(frame, "BTC")
        if asset_record is None or btc_record is None:
            return []
        asset_prices.append(asset_record["spot_mid"])
        btc_prices.append(btc_record["spot_mid"])
    return [
        _change(asset_prices[index], asset_prices[index - horizon], f"{asset}.residual_asset")
        - beta * _change(btc_prices[index], btc_prices[index - horizon], f"{asset}.residual_btc")
        for index in range(horizon, len(asset_prices))
    ]


def _event_key(asset: str, family: str, features: dict[str, float]) -> str:
    trigger = {
        "asset": asset,
        "family": family,
        "features": {key: round(value, 12) for key, value in sorted(features.items())},
    }
    return hashlib.sha256(canonical_json(trigger).encode("utf-8")).hexdigest()


def _feature_set(frames: list[v21.SnapshotFrame], asset: str) -> dict[str, float] | None:
    current = _asset_values(frames[-1], asset)
    one = _asset_values(frames[-2], asset)
    three = _asset_values(frames[-4], asset)
    six = _asset_values(frames[-7], asset)
    day = _asset_values(frames[-25], asset)
    btc_current = _asset_values(frames[-1], "BTC")
    btc_six = _asset_values(frames[-7], "BTC")
    btc_day = _asset_values(frames[-25], "BTC")
    required = (current, one, three, six, day, btc_current, btc_six, btc_day)
    if any(item is None for item in required):
        return None
    assert current is not None and one is not None and three is not None
    assert six is not None and day is not None
    assert btc_current is not None and btc_six is not None and btc_day is not None

    asset_hourly = _hourly_returns(frames, asset)
    btc_hourly = _hourly_returns(frames, "BTC")
    if not asset_hourly or not btc_hourly:
        return None
    beta = _beta(asset_hourly, btc_hourly)
    prior_frames = frames[:-1]
    residual_6h_history = _rolling_residuals(prior_frames, asset, beta, 6)
    residual_24h_history = _rolling_residuals(prior_frames, asset, beta, 24)
    if not residual_6h_history or not residual_24h_history:
        return None

    historical_records: list[dict[str, float]] = []
    for frame in frames[:-1]:
        record = _asset_values(frame, asset)
        if record is None:
            return None
        historical_records.append(record)
    sweep_history = historical_records[:-1]
    if not sweep_history:
        return None

    spot_return_1h = _change(current["spot_mid"], one["spot_mid"], f"{asset}.return_1h")
    spot_return_3h = _change(current["spot_mid"], three["spot_mid"], f"{asset}.return_3h")
    spot_return_6h = _change(current["spot_mid"], six["spot_mid"], f"{asset}.return_6h")
    spot_return_24h = _change(current["spot_mid"], day["spot_mid"], f"{asset}.return_24h")
    btc_return_6h = _change(btc_current["spot_mid"], btc_six["spot_mid"], "BTC.return_6h")
    btc_return_24h = _change(btc_current["spot_mid"], btc_day["spot_mid"], "BTC.return_24h")
    residual_6h = spot_return_6h - beta * btc_return_6h
    residual_24h = spot_return_24h - beta * btc_return_24h
    spread_contraction = (one["spot_spread_bps"] - current["spot_spread_bps"]) / max(
        one["spot_spread_bps"], 1e-12
    )

    return {
        **current,
        "prior_spot_spread_bps": one["spot_spread_bps"],
        "prior_spot_book_notional": one["spot_book_notional"],
        "spot_book_notional_change_1h": _change(
            current["spot_book_notional"], one["spot_book_notional"], f"{asset}.book_notional_1h"
        ),
        "spread_contraction_fraction": spread_contraction,
        "spot_return_1h": spot_return_1h,
        "spot_return_3h": spot_return_3h,
        "spot_return_6h": spot_return_6h,
        "spot_return_24h": spot_return_24h,
        "open_interest_change_6h": _change(
            current["open_interest_base"], six["open_interest_base"], f"{asset}.oi_6h"
        ),
        "prior_basis_bps": one["basis_bps"],
        "basis_6h_ago_bps": six["basis_bps"],
        "basis_change_1h_bps": current["basis_bps"] - one["basis_bps"],
        "prior_funding": one["funding"],
        "funding_6h_ago": six["funding"],
        "basis_p10": _percentile([record["basis_bps"] for record in historical_records], 0.10),
        "funding_p10": _percentile([record["funding"] for record in historical_records], 0.10),
        "prior_spread_p80": _percentile([record["spot_spread_bps"] for record in sweep_history], 0.80),
        "prior_book_notional_p20": _percentile(
            [record["spot_book_notional"] for record in sweep_history], 0.20
        ),
        "beta_to_btc": beta,
        "residual_return_6h": residual_6h,
        "residual_return_24h": residual_24h,
        "residual_6h_p80": _percentile(residual_6h_history, 0.80),
        "residual_24h_p80": _percentile(residual_24h_history, 0.80),
        "spot_flow_lead": current["spot_taker_imbalance"] - current["perp_flow_imbalance"],
    }


def _residual_momentum_candidate(
    asset: str,
    f: dict[str, float],
    controls: dict[str, Any],
) -> HighConvictionCandidate | None:
    if asset == "BTC":
        return None
    amplitude = f["residual_return_6h"]
    conditions = _conditions(
        positive_raw_6h=f["spot_return_6h"] > 0.0,
        positive_raw_24h=f["spot_return_24h"] > 0.0,
        residual_6h=f["residual_return_6h"] >= 0.008,
        residual_24h=f["residual_return_24h"] >= 0.015,
        residual_6h_percentile=f["residual_return_6h"] >= f["residual_6h_p80"],
        residual_24h_percentile=f["residual_return_24h"] >= f["residual_24h_p80"],
        positive_spot_flow=f["spot_taker_imbalance"] >= 0.12,
        positive_spot_book=f["spot_book_imbalance"] >= 0.04,
        spot_leads_perp=f["spot_flow_lead"] >= 0.08,
        bounded_basis=abs(f["basis_bps"]) <= 20.0,
        bounded_funding=f["funding"] <= 0.00010,
        bounded_open_interest=-0.03 <= f["open_interest_change_6h"] <= 0.08,
        risk_controls_permit=float(controls["total_exposure_cap"]) > 0.0,
        edge_to_cost=amplitude >= 3.0 * STANDARD_ROUND_TRIP_COST,
    )
    if not all(conditions.values()):
        return None
    score = sum((
        _clip(f["residual_return_6h"] / 0.008),
        _clip(f["residual_return_24h"] / 0.015),
        _clip(f["spot_taker_imbalance"] / 0.12),
        _clip(f["spot_book_imbalance"] / 0.04),
        _clip(f["spot_flow_lead"] / 0.08),
    ))
    family = "residual_momentum_microstructure"
    return HighConvictionCandidate(
        asset, family, score, amplitude, conditions, f, _event_key(asset, family, f)
    )


def _funding_transition_candidate(
    asset: str,
    f: dict[str, float],
    controls: dict[str, Any],
) -> HighConvictionCandidate | None:
    prior_stress = (
        f["funding_6h_ago"] <= f["funding_p10"]
        or (f["basis_6h_ago_bps"] < 0.0 and f["basis_6h_ago_bps"] <= f["basis_p10"])
    )
    amplitude = (
        abs(min(0.0, f["basis_6h_ago_bps"])) + max(0.0, f["basis_change_1h_bps"])
    ) / 10_000.0
    conditions = _conditions(
        prior_derivatives_stress=prior_stress,
        basis_improving=f["basis_change_1h_bps"] >= 3.0,
        funding_not_worsening=f["funding"] >= f["prior_funding"],
        bounded_funding=f["funding"] <= 0.00005,
        open_interest_contraction=f["open_interest_change_6h"] <= -0.03,
        positive_latest_hour=f["spot_return_1h"] > 0.0,
        positive_spot_flow=f["spot_taker_imbalance"] >= 0.10,
        positive_spot_book=f["spot_book_imbalance"] >= 0.03,
        spot_leads_perp=f["spot_flow_lead"] >= 0.05,
        bounded_current_basis=f["basis_bps"] <= 10.0,
        macro_not_blocked=not bool(controls["macro_blocked"]),
        edge_to_cost=amplitude >= 3.0 * STANDARD_ROUND_TRIP_COST,
    )
    if not all(conditions.values()):
        return None
    score = sum((
        _clip(abs(min(0.0, f["basis_6h_ago_bps"])) / 60.0),
        _clip(f["basis_change_1h_bps"] / 3.0),
        _clip(abs(min(0.0, f["open_interest_change_6h"])) / 0.03),
        _clip(f["spot_taker_imbalance"] / 0.10),
        _clip(f["spot_flow_lead"] / 0.05),
    ))
    family = "funding_basis_state_transition"
    return HighConvictionCandidate(
        asset, family, score, amplitude, conditions, f, _event_key(asset, family, f)
    )


def _sweep_replenishment_candidate(
    asset: str,
    f: dict[str, float],
    controls: dict[str, Any],
) -> HighConvictionCandidate | None:
    amplitude = min(f["spot_return_3h"], f["spread_contraction_fraction"])
    conditions = _conditions(
        prior_spread_shock=f["prior_spot_spread_bps"] >= f["prior_spread_p80"],
        prior_depth_vacuum=f["prior_spot_book_notional"] <= f["prior_book_notional_p20"],
        book_replenished=f["spot_book_notional_change_1h"] >= 0.25,
        spread_recovered=f["spread_contraction_fraction"] >= 0.20,
        bounded_current_spread=f["spot_spread_bps"] <= 15.0,
        positive_spot_flow=f["spot_taker_imbalance"] >= 0.15,
        positive_spot_book=f["spot_book_imbalance"] >= 0.05,
        spot_leads_perp=f["spot_flow_lead"] >= 0.10,
        positive_one_hour_return=f["spot_return_1h"] > 0.0,
        controlled_three_hour_return=0.0 < f["spot_return_3h"] <= 0.05,
        bounded_basis=abs(f["basis_bps"]) <= 20.0,
        bounded_funding=f["funding"] <= 0.00010,
        bounded_open_interest=f["open_interest_change_6h"] <= 0.08,
        risk_controls_permit=float(controls["total_exposure_cap"]) > 0.0,
        edge_to_cost=amplitude >= 3.0 * STANDARD_ROUND_TRIP_COST,
    )
    if not all(conditions.values()):
        return None
    score = sum((
        _clip(f["prior_spot_spread_bps"] / max(f["prior_spread_p80"], 1e-6)),
        _clip(f["spot_book_notional_change_1h"] / 0.25),
        _clip(f["spread_contraction_fraction"] / 0.20),
        _clip(f["spot_taker_imbalance"] / 0.15),
        _clip(f["spot_flow_lead"] / 0.10),
    ))
    family = "sweep_replenishment_continuation"
    return HighConvictionCandidate(
        asset, family, score, amplitude, conditions, f, _event_key(asset, family, f)
    )


def implementation_fingerprints() -> dict[str, str]:
    source_path = Path(__file__).resolve()
    dependency_path = Path(v21.__file__).resolve()
    if not PROTOCOL_PATH.exists():
        raise ForwardAlphaV25Error(f"Protocol file is missing: {PROTOCOL_PATH}")
    return {
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "protocol_sha256": hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest(),
        "v21_dependency_sha256": hashlib.sha256(dependency_path.read_bytes()).hexdigest(),
    }


def _finalize(report: dict[str, Any]) -> dict[str, Any]:
    finalized = dict(report)
    finalized["report_sha256"] = hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
    return finalized


def _snapshot_inventory(frames: list[v21.SnapshotFrame]) -> list[dict[str, str]]:
    return [
        {
            "snapshot_id": frame.snapshot_id,
            "record_sha256": frame.record_sha256,
            "hour": _hour(frame.hour),
        }
        for frame in frames
    ]


def _cash_report(
    frames: list[v21.SnapshotFrame],
    *,
    reason: str,
    missing_hours: list[str] | None = None,
) -> dict[str, Any]:
    current = frames[-1] if frames else None
    return _finalize({
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "candidate_state": "CASH",
        "decision_reason": reason,
        "data_cutoff_utc": None if current is None else _hour(current.hour),
        "intended_next_cycle_utc": None if current is None else _hour(current.hour + timedelta(hours=1)),
        "input_snapshot_count": len(frames),
        "input_snapshots": _snapshot_inventory(frames),
        "missing_required_hours": missing_hours or [],
        "global_controls": {},
        "asset_diagnostics": {},
        "qualified_candidates": [],
        "selected_candidates": [],
        "target_weights": {},
        "minimum_cash_weight": 1.0,
        "correlation_filter": None,
        "fingerprints": implementation_fingerprints(),
    })


def evaluate_forward_alpha_v25(
    frames: list[v21.SnapshotFrame],
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    if not frames:
        raise ForwardAlphaV25Error("At least one snapshot is required")
    ordered = sorted(frames, key=lambda frame: (frame.hour, frame.captured_at, frame.snapshot_id))
    earliest_by_hour: dict[datetime, v21.SnapshotFrame] = {}
    for frame in ordered:
        prior = earliest_by_hour.get(frame.hour)
        if prior is None or (frame.captured_at, frame.snapshot_id) < (prior.captured_at, prior.snapshot_id):
            earliest_by_hour[frame.hour] = frame
    ordered = [earliest_by_hour[hour] for hour in sorted(earliest_by_hour)]

    if as_of is not None:
        cutoff = as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=timezone.utc)
        cutoff = cutoff.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        ordered = [frame for frame in ordered if frame.hour <= cutoff]
        if not ordered:
            raise ForwardAlphaV25Error("No snapshots are available at or before as_of")

    current = ordered[-1]
    by_hour = {frame.hour: frame for frame in ordered}
    required_hours = [
        current.hour - timedelta(hours=offset)
        for offset in range(MIN_PRIOR_HOURS, -1, -1)
    ]
    missing = [_hour(hour) for hour in required_hours if hour not in by_hour]
    if missing:
        return _cash_report(
            ordered,
            reason="insufficient_contiguous_forward_history",
            missing_hours=missing,
        )

    used = [by_hour[hour] for hour in required_hours]
    controls = v21._global_controls(used[-1], used[0])
    diagnostics: dict[str, Any] = {}
    retained: list[HighConvictionCandidate] = []

    for asset in ASSETS:
        features = _feature_set(used, asset)
        if features is None:
            diagnostics[asset] = {
                "available": False,
                "reason": "one_or_more_required_factor_families_unavailable",
                "features": None,
                "families": {},
                "retained_family": None,
            }
            continue
        candidates = {
            "residual_momentum_microstructure": _residual_momentum_candidate(asset, features, controls),
            "funding_basis_state_transition": _funding_transition_candidate(asset, features, controls),
            "sweep_replenishment_continuation": _sweep_replenishment_candidate(asset, features, controls),
        }
        qualified = [candidate for candidate in candidates.values() if candidate is not None]
        best = sorted(qualified, key=lambda item: (-item.score, item.family))[0] if qualified else None
        diagnostics[asset] = {
            "available": True,
            "features": features,
            "families": {
                family: {
                    "qualified": candidate is not None,
                    "score": None if candidate is None else candidate.score,
                    "amplitude": None if candidate is None else candidate.amplitude,
                }
                for family, candidate in candidates.items()
            },
            "retained_family": None if best is None else best.family,
            "retained_score": None if best is None else best.score,
        }
        if best is not None:
            retained.append(best)

    ranked = sorted(retained, key=lambda item: (-item.score, item.asset, item.family))
    total_cap = min(0.30, float(controls["total_exposure_cap"]))
    selected = [] if total_cap <= 0.0 else ranked[:2]
    correlation_filter: dict[str, Any] | None = None
    if len(selected) == 2:
        correlation = v21._return_correlation(used, selected[0].asset, selected[1].asset)
        remove_second = correlation is None or correlation >= 0.80
        correlation_filter = {
            "assets": [selected[0].asset, selected[1].asset],
            "correlation_7d": correlation,
            "threshold": 0.80,
            "removed_second_asset": remove_second,
            "reason": (
                "correlation_unavailable"
                if correlation is None
                else "correlation_at_or_above_threshold"
                if remove_second
                else "passed"
            ),
        }
        if remove_second:
            selected = selected[:1]

    per_asset_weight = 0.0 if not selected else min(0.15, total_cap / len(selected))
    target_weights = {candidate.asset: per_asset_weight for candidate in selected}
    minimum_cash = 1.0 - sum(target_weights.values())
    if controls["macro_blocked"]:
        reason = "macro_risk_block"
    elif not ranked:
        reason = "no_family_qualified"
    elif not selected:
        reason = "global_exposure_cap_is_zero"
    else:
        reason = "one_or_more_frozen_v25_families_qualified"

    return _finalize({
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "candidate_state": "RESEARCH_CANDIDATES" if selected else "CASH",
        "decision_reason": reason,
        "data_cutoff_utc": _hour(current.hour),
        "intended_next_cycle_utc": _hour(current.hour + timedelta(hours=1)),
        "input_snapshot_count": len(used),
        "input_snapshots": _snapshot_inventory(used),
        "missing_required_hours": [],
        "global_controls": controls,
        "asset_diagnostics": diagnostics,
        "qualified_candidates": [
            {
                "asset": item.asset,
                "family": item.family,
                "score": item.score,
                "amplitude": item.amplitude,
                "event_key": item.event_key,
            }
            for item in ranked
        ],
        "selected_candidates": [
            {
                "asset": item.asset,
                "family": item.family,
                "score": item.score,
                "amplitude": item.amplitude,
                "event_key": item.event_key,
                "target_weight": target_weights[item.asset],
            }
            for item in selected
        ],
        "target_weights": target_weights,
        "minimum_cash_weight": minimum_cash,
        "correlation_filter": correlation_filter,
        "fingerprints": implementation_fingerprints(),
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate frozen paper-only v2.5 high-conviction forward alpha candidates."
    )
    parser.add_argument("--folder", required=True, help="Folder containing normalized v2.0 snapshots")
    parser.add_argument("--as-of", help="Optional UTC ISO timestamp; later snapshots are ignored")
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args(argv)
    as_of = None if args.as_of is None else v21._parse_utc(args.as_of, "as_of")
    report = evaluate_forward_alpha_v25(v21.load_forward_snapshots(args.folder), as_of=as_of)
    _atomic_json(Path(args.json_out), report)
    print(json.dumps({
        "candidate_state": report["candidate_state"],
        "decision_reason": report["decision_reason"],
        "data_cutoff_utc": report["data_cutoff_utc"],
        "selected_candidates": report["selected_candidates"],
        "minimum_cash_weight": report["minimum_cash_weight"],
        "report_sha256": report["report_sha256"],
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
