from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any


SCHEMA_VERSION = "2.1"
INPUT_SCHEMA_VERSION = "2.0"
ASSETS = ("BTC", "ETH", "SOL", "AVAX", "LINK", "DOGE")
PROTOCOL_PATH = Path("research/V21_MARKET_STATE_ROUTER_PROTOCOL.md")
MIN_PRIOR_HOURS = 168


class MarketStateRouterError(RuntimeError):
    """Raised when normalized forward snapshots violate the frozen router contract."""


@dataclass(frozen=True)
class SnapshotFrame:
    hour: datetime
    captured_at: datetime
    snapshot_id: str
    record_sha256: str
    assets: dict[str, Any]
    global_state: dict[str, Any]
    source_path: str


@dataclass(frozen=True)
class Candidate:
    asset: str
    sleeve: str
    score: float
    features: dict[str, float]
    conditions: dict[str, bool]


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise MarketStateRouterError(f"{field} must be a non-empty ISO timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise MarketStateRouterError(f"{field} is not valid ISO-8601: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketStateRouterError(f"{field} is not numeric") from exc
    if not math.isfinite(number):
        raise MarketStateRouterError(f"{field} is not finite")
    return number


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _validate_record_hash(payload: dict[str, Any]) -> str:
    expected = str(payload.get("record_sha256", ""))
    unhashed = dict(payload)
    unhashed.pop("record_sha256", None)
    actual = hashlib.sha256(canonical_json(unhashed).encode("utf-8")).hexdigest()
    if not expected or expected != actual:
        raise MarketStateRouterError("Snapshot record_sha256 does not match canonical content")
    return expected


def load_forward_snapshots(folder: str | Path) -> list[SnapshotFrame]:
    root = Path(folder)
    if not root.exists() or not root.is_dir():
        raise MarketStateRouterError(f"Snapshot folder does not exist: {root}")
    earliest_by_hour: dict[datetime, SnapshotFrame] = {}
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MarketStateRouterError(f"Unreadable snapshot {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise MarketStateRouterError(f"Snapshot root must be an object: {path}")
        if payload.get("schema_version") != INPUT_SCHEMA_VERSION:
            raise MarketStateRouterError(f"Unsupported snapshot schema in {path}")
        if payload.get("paper_only") is not True or payload.get("authorizes_trading") is not False:
            raise MarketStateRouterError(f"Unsafe snapshot flags in {path}")
        record_sha = _validate_record_hash(payload)
        hour = _parse_utc(payload.get("hour_bucket_utc"), "hour_bucket_utc")
        captured = _parse_utc(payload.get("captured_at_utc"), "captured_at_utc")
        if captured.replace(minute=0, second=0, microsecond=0) != hour:
            raise MarketStateRouterError(f"Capture/hour mismatch in {path}")
        assets = payload.get("assets")
        global_state = payload.get("global")
        if not isinstance(assets, dict) or not isinstance(global_state, dict):
            raise MarketStateRouterError(f"Snapshot is missing assets/global state: {path}")
        frame = SnapshotFrame(
            hour=hour,
            captured_at=captured,
            snapshot_id=str(payload.get("snapshot_id", "")),
            record_sha256=record_sha,
            assets=assets,
            global_state=global_state,
            source_path=path.as_posix(),
        )
        prior = earliest_by_hour.get(hour)
        if prior is None or (frame.captured_at, frame.snapshot_id) < (prior.captured_at, prior.snapshot_id):
            earliest_by_hour[hour] = frame
    if not earliest_by_hour:
        raise MarketStateRouterError(f"No normalized snapshots found in {root}")
    return [earliest_by_hour[hour] for hour in sorted(earliest_by_hour)]


def _available(record: dict[str, Any], family: str) -> dict[str, Any] | None:
    value = record.get(family)
    return value if isinstance(value, dict) and value.get("available") is True else None


def _asset_values(frame: SnapshotFrame, asset: str) -> dict[str, float] | None:
    record = frame.assets.get(asset)
    if not isinstance(record, dict):
        return None
    quote = _available(record, "spot_quote")
    book = _available(record, "spot_book")
    spot_flow = _available(record, "spot_trade_flow")
    perp = _available(record, "perp_state")
    perp_flow = _available(record, "perp_trade_flow")
    cross = _available(record, "cross_venue")
    if any(value is None for value in (quote, book, spot_flow, perp, perp_flow, cross)):
        return None
    assert quote is not None and book is not None and spot_flow is not None
    assert perp is not None and perp_flow is not None and cross is not None
    return {
        "spot_mid": _number(quote.get("mid"), f"{asset}.spot_mid"),
        "spot_book_imbalance": _number(book.get("imbalance"), f"{asset}.spot_book_imbalance"),
        "spot_taker_imbalance": _number(spot_flow.get("taker_imbalance"), f"{asset}.spot_taker_imbalance"),
        "funding": _number(perp.get("funding"), f"{asset}.funding"),
        "open_interest_base": _number(perp.get("open_interest_base"), f"{asset}.open_interest_base"),
        "perp_flow_imbalance": _number(perp_flow.get("reported_side_imbalance"), f"{asset}.perp_flow_imbalance"),
        "basis_bps": _number(cross.get("spot_perp_basis_bps"), f"{asset}.basis_bps"),
    }


def _change(current: float, prior: float, field: str) -> float:
    if prior <= 0:
        raise MarketStateRouterError(f"{field} prior value must be positive")
    return current / prior - 1.0


def _cap(value: float, maximum: float = 3.0) -> float:
    return max(0.0, min(maximum, value))


def _conditions(**items: bool) -> dict[str, bool]:
    return {name: bool(value) for name, value in items.items()}


def _candidate_capitulation(asset: str, f: dict[str, float]) -> Candidate | None:
    conditions = _conditions(
        spot_return_24h=f["spot_return_24h"] <= -0.035,
        open_interest_change_24h=f["open_interest_change_24h"] <= -0.08,
        spot_taker_imbalance=f["spot_taker_imbalance"] >= 0.15,
        spot_book_imbalance=f["spot_book_imbalance"] >= 0.05,
        hourly_recovery=f["spot_return_1h"] > 0.0,
        basis_recovery=f["basis_recovery_1h_bps"] >= 2.0,
        funding=f["funding"] <= 0.00005,
    )
    if not all(conditions.values()):
        return None
    score = sum((
        _cap(abs(f["spot_return_24h"]) / 0.035),
        _cap(abs(f["open_interest_change_24h"]) / 0.08),
        _cap(f["spot_taker_imbalance"]),
        _cap(f["spot_book_imbalance"]),
        _cap(f["basis_recovery_1h_bps"] / 2.0),
    ))
    return Candidate(asset, "capitulation_recovery_proxy", score, f, conditions)


def _candidate_basis(asset: str, f: dict[str, float]) -> Candidate | None:
    conditions = _conditions(
        prior_negative_basis=f["prior_basis_bps"] <= -20.0,
        basis_recovery=f["basis_recovery_1h_bps"] >= 5.0,
        bounded_current_basis=f["basis_bps"] <= 5.0,
        open_interest_change_24h=f["open_interest_change_24h"] <= -0.03,
        spot_taker_imbalance=f["spot_taker_imbalance"] >= 0.10,
        hourly_recovery=f["spot_return_1h"] > 0.0,
        funding=f["funding"] <= 0.0,
    )
    if not all(conditions.values()):
        return None
    score = sum((
        _cap(abs(f["prior_basis_bps"]) / 20.0),
        _cap(f["basis_recovery_1h_bps"] / 5.0),
        _cap(abs(f["open_interest_change_24h"]) / 0.03),
        _cap(f["spot_taker_imbalance"]),
    ))
    return Candidate(asset, "negative_basis_normalization", score, f, conditions)


def _candidate_continuation(asset: str, f: dict[str, float]) -> Candidate | None:
    flow_lead = f["spot_taker_imbalance"] - f["perp_flow_imbalance"]
    conditions = _conditions(
        spot_return_6h=0.015 <= f["spot_return_6h"] <= 0.06,
        spot_taker_imbalance=f["spot_taker_imbalance"] >= 0.20,
        spot_book_imbalance=f["spot_book_imbalance"] >= 0.10,
        spot_flow_lead=flow_lead >= 0.10,
        bounded_basis=abs(f["basis_bps"]) <= 15.0,
        open_interest_change_6h=-0.02 <= f["open_interest_change_6h"] <= 0.08,
        funding=f["funding"] <= 0.00010,
    )
    if not all(conditions.values()):
        return None
    score = sum((
        _cap(f["spot_return_6h"] / 0.015),
        _cap(f["spot_taker_imbalance"]),
        _cap(f["spot_book_imbalance"]),
        _cap(flow_lead),
        _cap((15.0 - abs(f["basis_bps"])) / 15.0),
    ))
    return Candidate(asset, "spot_led_continuation", score, f, conditions)


def _asset_features(
    asset: str,
    current: SnapshotFrame,
    prior_1h: SnapshotFrame,
    prior_6h: SnapshotFrame,
    prior_24h: SnapshotFrame,
) -> dict[str, float] | None:
    now = _asset_values(current, asset)
    one = _asset_values(prior_1h, asset)
    six = _asset_values(prior_6h, asset)
    day = _asset_values(prior_24h, asset)
    if any(value is None for value in (now, one, six, day)):
        return None
    assert now is not None and one is not None and six is not None and day is not None
    return {
        **now,
        "spot_return_1h": _change(now["spot_mid"], one["spot_mid"], f"{asset}.spot_return_1h"),
        "spot_return_6h": _change(now["spot_mid"], six["spot_mid"], f"{asset}.spot_return_6h"),
        "spot_return_24h": _change(now["spot_mid"], day["spot_mid"], f"{asset}.spot_return_24h"),
        "open_interest_change_6h": _change(now["open_interest_base"], six["open_interest_base"], f"{asset}.oi_6h"),
        "open_interest_change_24h": _change(now["open_interest_base"], day["open_interest_base"], f"{asset}.oi_24h"),
        "prior_basis_bps": one["basis_bps"],
        "basis_recovery_1h_bps": now["basis_bps"] - one["basis_bps"],
    }


def _global_value(frame: SnapshotFrame, family: str, key: str, metric: str, max_staleness: int) -> float | None:
    group = frame.global_state.get(family)
    if not isinstance(group, dict):
        return None
    record = group.get(key)
    if not isinstance(record, dict) or record.get("available") is not True:
        return None
    staleness = record.get("staleness_days")
    if staleness is None or int(staleness) > max_staleness:
        return None
    values = record.get("values")
    if not isinstance(values, dict) or metric not in values:
        return None
    return _number(values[metric], f"global.{family}.{key}.{metric}")


def _global_controls(current: SnapshotFrame, week: SnapshotFrame) -> dict[str, Any]:
    vix = _global_value(current, "fred", "VIXCLS", "VIXCLS", 7)
    dollar_now = _global_value(current, "fred", "DTWEXBGS", "DTWEXBGS", 7)
    dollar_week = _global_value(week, "fred", "DTWEXBGS", "DTWEXBGS", 7)
    dollar_change = None
    if dollar_now is not None and dollar_week is not None and dollar_week > 0:
        dollar_change = dollar_now / dollar_week - 1.0

    macro_available = vix is not None and dollar_change is not None
    macro_blocked = (vix is not None and vix > 35.0) or (dollar_change is not None and dollar_change > 0.02)

    stable_now: list[float] = []
    stable_week: list[float] = []
    for asset in ("USDT", "USDC"):
        now = _global_value(current, "coinmetrics", asset, "CapMrktCurUSD", 14)
        prior = _global_value(week, "coinmetrics", asset, "CapMrktCurUSD", 14)
        if now is not None and prior is not None:
            stable_now.append(now)
            stable_week.append(prior)
    stablecoin_growth = None
    if len(stable_now) == 2 and sum(stable_week) > 0:
        stablecoin_growth = sum(stable_now) / sum(stable_week) - 1.0
    stablecoin_available = stablecoin_growth is not None

    conservative = not macro_available or not stablecoin_available
    if stablecoin_growth is not None and stablecoin_growth < -0.01:
        conservative = True
    exposure_cap = 0.0 if macro_blocked else 0.25 if conservative else 0.50
    return {
        "vix": vix,
        "broad_dollar_change_7d": dollar_change,
        "stablecoin_market_cap_change_7d": stablecoin_growth,
        "macro_available": macro_available,
        "stablecoin_available": stablecoin_available,
        "macro_blocked": macro_blocked,
        "conservative_cap": conservative,
        "total_exposure_cap": exposure_cap,
    }


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


def _return_correlation(frames: list[SnapshotFrame], left_asset: str, right_asset: str) -> float | None:
    left_prices: list[float] = []
    right_prices: list[float] = []
    for frame in frames:
        left = _asset_values(frame, left_asset)
        right = _asset_values(frame, right_asset)
        if left is None or right is None:
            return None
        left_prices.append(left["spot_mid"])
        right_prices.append(right["spot_mid"])
    left_returns = [_change(left_prices[index], left_prices[index - 1], "correlation.left") for index in range(1, len(left_prices))]
    right_returns = [_change(right_prices[index], right_prices[index - 1], "correlation.right") for index in range(1, len(right_prices))]
    return _pearson(left_returns, right_returns)


def implementation_fingerprints() -> dict[str, str]:
    source_path = Path(__file__).resolve()
    if not PROTOCOL_PATH.exists():
        raise MarketStateRouterError(f"Protocol file is missing: {PROTOCOL_PATH}")
    return {
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "protocol_sha256": hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest(),
    }


def _sleeve_diagnostics(features: dict[str, float]) -> dict[str, dict[str, Any]]:
    flow_lead = features["spot_taker_imbalance"] - features["perp_flow_imbalance"]
    matrices = {
        "capitulation_recovery_proxy": _conditions(
            spot_return_24h=features["spot_return_24h"] <= -0.035,
            open_interest_change_24h=features["open_interest_change_24h"] <= -0.08,
            spot_taker_imbalance=features["spot_taker_imbalance"] >= 0.15,
            spot_book_imbalance=features["spot_book_imbalance"] >= 0.05,
            hourly_recovery=features["spot_return_1h"] > 0.0,
            basis_recovery=features["basis_recovery_1h_bps"] >= 2.0,
            funding=features["funding"] <= 0.00005,
        ),
        "negative_basis_normalization": _conditions(
            prior_negative_basis=features["prior_basis_bps"] <= -20.0,
            basis_recovery=features["basis_recovery_1h_bps"] >= 5.0,
            bounded_current_basis=features["basis_bps"] <= 5.0,
            open_interest_change_24h=features["open_interest_change_24h"] <= -0.03,
            spot_taker_imbalance=features["spot_taker_imbalance"] >= 0.10,
            hourly_recovery=features["spot_return_1h"] > 0.0,
            funding=features["funding"] <= 0.0,
        ),
        "spot_led_continuation": _conditions(
            spot_return_6h=0.015 <= features["spot_return_6h"] <= 0.06,
            spot_taker_imbalance=features["spot_taker_imbalance"] >= 0.20,
            spot_book_imbalance=features["spot_book_imbalance"] >= 0.10,
            spot_flow_lead=flow_lead >= 0.10,
            bounded_basis=abs(features["basis_bps"]) <= 15.0,
            open_interest_change_6h=-0.02 <= features["open_interest_change_6h"] <= 0.08,
            funding=features["funding"] <= 0.00010,
        ),
    }
    return {
        name: {
            "qualified": all(conditions.values()),
            "conditions": conditions,
            "failed_conditions": [key for key, value in conditions.items() if not value],
        }
        for name, conditions in matrices.items()
    }


def _finalize_report(report: dict[str, Any]) -> dict[str, Any]:
    finalized = dict(report)
    finalized["report_sha256"] = hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
    return finalized


def _cash_report(
    frames: list[SnapshotFrame],
    *,
    reason: str,
    missing_hours: list[str] | None = None,
) -> dict[str, Any]:
    last = frames[-1] if frames else None
    report = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "authorizes_trading": False,
        "candidate_state": "CASH",
        "decision_reason": reason,
        "data_cutoff_utc": None if last is None else last.hour.isoformat().replace("+00:00", "Z"),
        "intended_next_cycle_utc": None if last is None else (last.hour + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "input_snapshot_count": len(frames),
        "input_snapshots": [
            {"snapshot_id": frame.snapshot_id, "record_sha256": frame.record_sha256, "hour": frame.hour.isoformat().replace("+00:00", "Z")}
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
    }
    return _finalize_report(report)


def evaluate_market_state_router(
    frames: list[SnapshotFrame],
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    if not frames:
        raise MarketStateRouterError("At least one snapshot is required")
    ordered = sorted(frames, key=lambda frame: frame.hour)
    if as_of is not None:
        cutoff = as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=timezone.utc)
        cutoff = cutoff.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        ordered = [frame for frame in ordered if frame.hour <= cutoff]
        if not ordered:
            raise MarketStateRouterError("No snapshots are available at or before as_of")
    current = ordered[-1]
    by_hour = {frame.hour: frame for frame in ordered}
    required_hours = [current.hour - timedelta(hours=offset) for offset in range(MIN_PRIOR_HOURS, -1, -1)]
    missing = [hour.isoformat().replace("+00:00", "Z") for hour in required_hours if hour not in by_hour]
    if missing:
        return _cash_report(ordered, reason="insufficient_contiguous_forward_history", missing_hours=missing)

    used = [by_hour[hour] for hour in required_hours]
    prior_1h = by_hour[current.hour - timedelta(hours=1)]
    prior_6h = by_hour[current.hour - timedelta(hours=6)]
    prior_24h = by_hour[current.hour - timedelta(hours=24)]
    prior_7d = by_hour[current.hour - timedelta(hours=168)]
    controls = _global_controls(current, prior_7d)

    diagnostics: dict[str, Any] = {}
    candidates: list[Candidate] = []
    for asset in ASSETS:
        features = _asset_features(asset, current, prior_1h, prior_6h, prior_24h)
        if features is None:
            diagnostics[asset] = {
                "available": False,
                "reason": "one_or_more_required_factor_families_unavailable",
                "features": None,
                "sleeves": {},
            }
            continue
        sleeve_diagnostics = _sleeve_diagnostics(features)
        asset_candidates = [
            candidate
            for candidate in (
                _candidate_capitulation(asset, features),
                _candidate_basis(asset, features),
                _candidate_continuation(asset, features),
            )
            if candidate is not None
        ]
        best = sorted(asset_candidates, key=lambda item: (-item.score, item.sleeve))[0] if asset_candidates else None
        diagnostics[asset] = {
            "available": True,
            "features": features,
            "sleeves": sleeve_diagnostics,
            "retained_sleeve": None if best is None else best.sleeve,
            "retained_score": None if best is None else best.score,
        }
        if best is not None:
            candidates.append(best)

    ranked = sorted(candidates, key=lambda item: (-item.score, item.asset, item.sleeve))
    selected = [] if controls["total_exposure_cap"] <= 0 else ranked[:2]
    correlation_filter: dict[str, Any] | None = None
    if len(selected) == 2:
        correlation = _return_correlation(used, selected[0].asset, selected[1].asset)
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

    exposure_cap = float(controls["total_exposure_cap"])
    per_asset_weight = 0.0 if not selected else min(0.25, exposure_cap / len(selected))
    target_weights = {candidate.asset: per_asset_weight for candidate in selected}
    minimum_cash = 1.0 - sum(target_weights.values())
    state = "RESEARCH_CANDIDATES" if selected else "CASH"
    if controls["macro_blocked"]:
        reason = "macro_risk_block"
    elif not ranked:
        reason = "no_sleeve_qualified"
    elif not selected:
        reason = "global_exposure_cap_is_zero"
    else:
        reason = "one_or_more_frozen_sleeves_qualified"

    report = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "authorizes_trading": False,
        "candidate_state": state,
        "decision_reason": reason,
        "data_cutoff_utc": current.hour.isoformat().replace("+00:00", "Z"),
        "intended_next_cycle_utc": (current.hour + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "input_snapshot_count": len(used),
        "input_snapshots": [
            {"snapshot_id": frame.snapshot_id, "record_sha256": frame.record_sha256, "hour": frame.hour.isoformat().replace("+00:00", "Z")}
            for frame in used
        ],
        "missing_required_hours": [],
        "global_controls": controls,
        "asset_diagnostics": diagnostics,
        "qualified_candidates": [
            {"asset": item.asset, "sleeve": item.sleeve, "score": item.score}
            for item in ranked
        ],
        "selected_candidates": [
            {"asset": item.asset, "sleeve": item.sleeve, "score": item.score, "target_weight": target_weights[item.asset]}
            for item in selected
        ],
        "target_weights": target_weights,
        "minimum_cash_weight": minimum_cash,
        "correlation_filter": correlation_filter,
        "fingerprints": implementation_fingerprints(),
    }
    return _finalize_report(report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the paper-only v2.1 forward market-state router.")
    parser.add_argument("--folder", required=True, help="Folder containing normalized v2.0 snapshot JSON files")
    parser.add_argument("--as-of", help="Optional UTC ISO timestamp; later snapshots are ignored")
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args(argv)
    as_of = None if args.as_of is None else _parse_utc(args.as_of, "as_of")
    report = evaluate_market_state_router(load_forward_snapshots(args.folder), as_of=as_of)
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
