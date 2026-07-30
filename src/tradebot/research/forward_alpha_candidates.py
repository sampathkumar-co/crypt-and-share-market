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


SCHEMA_VERSION = "2.3"
ASSETS = v21.ASSETS
MIN_PRIOR_HOURS = 168
PROTOCOL_PATH = Path("research/V23_FORWARD_ALPHA_CANDIDATES_PROTOCOL.md")


class ForwardAlphaError(RuntimeError):
    """Raised when v2.3 forward evidence violates the frozen contract."""


@dataclass(frozen=True)
class AlphaCandidate:
    asset: str
    family: str
    score: float
    conditions: dict[str, bool]
    features: dict[str, float]


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _hour(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _percentile(values: list[float], probability: float) -> float:
    if not values:
        raise ForwardAlphaError("Cannot calculate a percentile from an empty sample")
    if not 0.0 <= probability <= 1.0:
        raise ForwardAlphaError("Percentile probability must be in [0, 1]")
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


def _asset_values(frame: v21.SnapshotFrame, asset: str) -> dict[str, float] | None:
    return v21._asset_values(frame, asset)


def _change(current: float, prior: float, field: str) -> float:
    return v21._change(current, prior, field)


def _rolling_returns(frames: list[v21.SnapshotFrame], asset: str, horizon: int) -> list[float]:
    values: list[float] = []
    prices: list[float] = []
    for frame in frames:
        record = _asset_values(frame, asset)
        if record is None:
            return []
        prices.append(record["spot_mid"])
    for index in range(horizon, len(prices)):
        values.append(_change(prices[index], prices[index - horizon], f"{asset}.rolling_return_{horizon}h"))
    return values


def _feature_set(frames: list[v21.SnapshotFrame], asset: str) -> dict[str, float] | None:
    current = _asset_values(frames[-1], asset)
    one = _asset_values(frames[-2], asset)
    three = _asset_values(frames[-4], asset)
    six = _asset_values(frames[-7], asset)
    day = _asset_values(frames[-25], asset)
    if any(item is None for item in (current, one, three, six, day)):
        return None
    assert current is not None and one is not None and three is not None and six is not None and day is not None

    trailing_basis: list[float] = []
    latest_three_spot_flows: list[float] = []
    for frame in frames[-25:-1]:
        record = _asset_values(frame, asset)
        if record is None:
            return None
        trailing_basis.append(record["basis_bps"])
    for frame in frames[-3:]:
        record = _asset_values(frame, asset)
        if record is None:
            return None
        latest_three_spot_flows.append(record["spot_taker_imbalance"])

    returns_6h = _rolling_returns(frames[:-1], asset, 6)
    returns_24h = _rolling_returns(frames[:-1], asset, 24)
    if not returns_6h or not returns_24h:
        return None

    return {
        **current,
        "spot_return_1h": _change(current["spot_mid"], one["spot_mid"], f"{asset}.return_1h"),
        "spot_return_3h": _change(current["spot_mid"], three["spot_mid"], f"{asset}.return_3h"),
        "spot_return_6h": _change(current["spot_mid"], six["spot_mid"], f"{asset}.return_6h"),
        "spot_return_24h": _change(current["spot_mid"], day["spot_mid"], f"{asset}.return_24h"),
        "open_interest_change_6h": _change(current["open_interest_base"], six["open_interest_base"], f"{asset}.oi_6h"),
        "open_interest_change_24h": _change(current["open_interest_base"], day["open_interest_base"], f"{asset}.oi_24h"),
        "prior_basis_bps": one["basis_bps"],
        "basis_change_1h_bps": current["basis_bps"] - one["basis_bps"],
        "prior_funding": one["funding"],
        "basis_p10_24h": _percentile(trailing_basis, 0.10),
        "return_6h_p10_7d": _percentile(returns_6h, 0.10),
        "return_24h_p10_7d": _percentile(returns_24h, 0.10),
        "return_6h_p90_7d": _percentile(returns_6h, 0.90),
        "positive_spot_flow_hours_3h": float(sum(value > 0.0 for value in latest_three_spot_flows)),
        "spot_flow_lead": current["spot_taker_imbalance"] - current["perp_flow_imbalance"],
    }


def _cross_venue_candidate(asset: str, f: dict[str, float]) -> AlphaCandidate | None:
    conditions = _conditions(
        materially_negative_basis=f["basis_bps"] < 0.0 and f["basis_bps"] <= f["basis_p10_24h"],
        basis_improving=f["basis_change_1h_bps"] >= 2.0,
        positive_spot_flow=f["spot_taker_imbalance"] >= 0.10,
        positive_spot_book=f["spot_book_imbalance"] >= 0.03,
        spot_leads_perp=f["spot_flow_lead"] >= 0.05,
        nonexpanding_open_interest=f["open_interest_change_6h"] <= 0.01,
        bounded_funding=f["funding"] <= 0.00005,
    )
    if not all(conditions.values()):
        return None
    dislocation = abs(min(0.0, f["basis_bps"])) / max(5.0, abs(f["basis_p10_24h"]))
    score = sum((
        _clip(dislocation),
        _clip(f["basis_change_1h_bps"] / 2.0),
        _clip(f["spot_taker_imbalance"] / 0.10),
        _clip(f["spot_book_imbalance"] / 0.03),
        _clip(f["spot_flow_lead"] / 0.05),
    ))
    return AlphaCandidate(asset, "cross_venue_dislocation_normalization", score, conditions, f)


def _liquidity_recovery_candidate(
    asset: str,
    f: dict[str, float],
    controls: dict[str, Any],
) -> AlphaCandidate | None:
    extreme_6h = f["spot_return_6h"] <= min(-0.02, f["return_6h_p10_7d"])
    extreme_24h = f["spot_return_24h"] <= min(-0.04, f["return_24h_p10_7d"])
    conditions = _conditions(
        extreme_completed_decline=extreme_6h or extreme_24h,
        open_interest_contraction=(
            f["open_interest_change_6h"] <= -0.04
            if extreme_6h
            else f["open_interest_change_24h"] <= -0.08
        ),
        positive_latest_hour=f["spot_return_1h"] > 0.0,
        positive_spot_flow=f["spot_taker_imbalance"] >= 0.12,
        positive_spot_book=f["spot_book_imbalance"] >= 0.04,
        basis_not_deteriorating=f["basis_change_1h_bps"] >= -1.0,
        funding_not_deteriorating=f["funding"] <= f["prior_funding"] + 0.00002,
        macro_not_blocked=not bool(controls["macro_blocked"]),
    )
    if not all(conditions.values()):
        return None
    decline_floor = abs(f["return_6h_p10_7d"] if extreme_6h else f["return_24h_p10_7d"])
    observed_decline = abs(f["spot_return_6h"] if extreme_6h else f["spot_return_24h"])
    oi_contraction = abs(min(0.0, f["open_interest_change_6h"] if extreme_6h else f["open_interest_change_24h"]))
    score = sum((
        _clip(observed_decline / max(0.01, decline_floor)),
        _clip(oi_contraction / (0.04 if extreme_6h else 0.08)),
        _clip(f["spot_return_1h"] / 0.005),
        _clip(f["spot_taker_imbalance"] / 0.12),
        _clip(f["spot_book_imbalance"] / 0.04),
    ))
    return AlphaCandidate(asset, "liquidity_vacuum_recovery", score, conditions, f)


def _spot_flow_candidate(
    asset: str,
    f: dict[str, float],
    controls: dict[str, Any],
) -> AlphaCandidate | None:
    dynamic_overextension = max(0.04, f["return_6h_p90_7d"] * 1.25)
    conditions = _conditions(
        positive_three_hour_return=f["spot_return_3h"] >= 0.006,
        positive_six_hour_return=f["spot_return_6h"] >= 0.012,
        not_overextended=f["spot_return_6h"] <= min(0.07, dynamic_overextension),
        persistent_spot_flow=f["positive_spot_flow_hours_3h"] >= 2.0,
        positive_spot_book=f["spot_book_imbalance"] >= 0.05,
        spot_leads_perp=f["spot_flow_lead"] >= 0.10,
        bounded_basis=abs(f["basis_bps"]) <= 20.0,
        bounded_funding=f["funding"] <= 0.00010,
        bounded_open_interest=-0.03 <= f["open_interest_change_6h"] <= 0.08,
        risk_controls_permit=float(controls["total_exposure_cap"]) > 0.0,
    )
    if not all(conditions.values()):
        return None
    score = sum((
        _clip(f["spot_return_3h"] / 0.006),
        _clip(f["spot_return_6h"] / 0.012),
        _clip(f["positive_spot_flow_hours_3h"] / 2.0),
        _clip(f["spot_book_imbalance"] / 0.05),
        _clip(f["spot_flow_lead"] / 0.10),
    ))
    return AlphaCandidate(asset, "spot_led_flow_persistence", score, conditions, f)


def implementation_fingerprints() -> dict[str, str]:
    source_path = Path(__file__).resolve()
    dependency_path = Path(v21.__file__).resolve()
    if not PROTOCOL_PATH.exists():
        raise ForwardAlphaError(f"Protocol file is missing: {PROTOCOL_PATH}")
    return {
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "protocol_sha256": hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest(),
        "v21_dependency_sha256": hashlib.sha256(dependency_path.read_bytes()).hexdigest(),
    }


def _finalize(report: dict[str, Any]) -> dict[str, Any]:
    finalized = dict(report)
    finalized["report_sha256"] = hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
    return finalized


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
        "candidate_state": "CASH",
        "decision_reason": reason,
        "data_cutoff_utc": None if current is None else _hour(current.hour),
        "intended_next_cycle_utc": None if current is None else _hour(current.hour + timedelta(hours=1)),
        "input_snapshot_count": len(frames),
        "input_snapshots": [
            {"snapshot_id": frame.snapshot_id, "record_sha256": frame.record_sha256, "hour": _hour(frame.hour)}
            for frame in frames
        ],
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


def evaluate_forward_alpha(
    frames: list[v21.SnapshotFrame],
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    if not frames:
        raise ForwardAlphaError("At least one snapshot is required")
    ordered = sorted(frames, key=lambda frame: frame.hour)
    if as_of is not None:
        cutoff = as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=timezone.utc)
        cutoff = cutoff.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        ordered = [frame for frame in ordered if frame.hour <= cutoff]
        if not ordered:
            raise ForwardAlphaError("No snapshots are available at or before as_of")

    current = ordered[-1]
    by_hour = {frame.hour: frame for frame in ordered}
    required_hours = [current.hour - timedelta(hours=offset) for offset in range(MIN_PRIOR_HOURS, -1, -1)]
    missing = [_hour(hour) for hour in required_hours if hour not in by_hour]
    if missing:
        return _cash_report(ordered, reason="insufficient_contiguous_forward_history", missing_hours=missing)

    used = [by_hour[hour] for hour in required_hours]
    controls = v21._global_controls(used[-1], used[0])
    diagnostics: dict[str, Any] = {}
    retained: list[AlphaCandidate] = []

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
        family_candidates = [
            candidate
            for candidate in (
                _cross_venue_candidate(asset, features),
                _liquidity_recovery_candidate(asset, features, controls),
                _spot_flow_candidate(asset, features, controls),
            )
            if candidate is not None
        ]
        best = sorted(family_candidates, key=lambda item: (-item.score, item.family))[0] if family_candidates else None
        diagnostics[asset] = {
            "available": True,
            "features": features,
            "families": {
                "cross_venue_dislocation_normalization": {
                    "qualified": _cross_venue_candidate(asset, features) is not None,
                },
                "liquidity_vacuum_recovery": {
                    "qualified": _liquidity_recovery_candidate(asset, features, controls) is not None,
                },
                "spot_led_flow_persistence": {
                    "qualified": _spot_flow_candidate(asset, features, controls) is not None,
                },
            },
            "retained_family": None if best is None else best.family,
            "retained_score": None if best is None else best.score,
        }
        if best is not None:
            retained.append(best)

    ranked = sorted(retained, key=lambda item: (-item.score, item.asset, item.family))
    total_cap = min(0.40, float(controls["total_exposure_cap"]))
    selected = [] if total_cap <= 0.0 else ranked[:2]
    correlation_filter: dict[str, Any] | None = None
    if len(selected) == 2:
        correlation = v21._return_correlation(used, selected[0].asset, selected[1].asset)
        remove_second = correlation is None or correlation >= 0.85
        correlation_filter = {
            "assets": [selected[0].asset, selected[1].asset],
            "correlation_7d": correlation,
            "threshold": 0.85,
            "removed_second_asset": remove_second,
            "reason": "correlation_unavailable" if correlation is None else "correlation_at_or_above_threshold" if remove_second else "passed",
        }
        if remove_second:
            selected = selected[:1]

    per_asset_weight = 0.0 if not selected else min(0.20, total_cap / len(selected))
    target_weights = {candidate.asset: per_asset_weight for candidate in selected}
    minimum_cash = 1.0 - sum(target_weights.values())
    if controls["macro_blocked"]:
        reason = "macro_risk_block"
    elif not ranked:
        reason = "no_family_qualified"
    elif not selected:
        reason = "global_exposure_cap_is_zero"
    else:
        reason = "one_or_more_frozen_v23_families_qualified"

    return _finalize({
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "authorizes_trading": False,
        "candidate_state": "RESEARCH_CANDIDATES" if selected else "CASH",
        "decision_reason": reason,
        "data_cutoff_utc": _hour(current.hour),
        "intended_next_cycle_utc": _hour(current.hour + timedelta(hours=1)),
        "input_snapshot_count": len(used),
        "input_snapshots": [
            {"snapshot_id": frame.snapshot_id, "record_sha256": frame.record_sha256, "hour": _hour(frame.hour)}
            for frame in used
        ],
        "missing_required_hours": [],
        "global_controls": controls,
        "asset_diagnostics": diagnostics,
        "qualified_candidates": [
            {"asset": item.asset, "family": item.family, "score": item.score}
            for item in ranked
        ],
        "selected_candidates": [
            {
                "asset": item.asset,
                "family": item.family,
                "score": item.score,
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
    parser = argparse.ArgumentParser(description="Evaluate frozen paper-only v2.3 forward alpha candidates.")
    parser.add_argument("--folder", required=True, help="Folder containing normalized v2.0 snapshot JSON files")
    parser.add_argument("--as-of", help="Optional UTC ISO timestamp; later snapshots are ignored")
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args(argv)
    as_of = None if args.as_of is None else v21._parse_utc(args.as_of, "as_of")
    report = evaluate_forward_alpha(v21.load_forward_snapshots(args.folder), as_of=as_of)
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
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
