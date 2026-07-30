from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tradebot.research.forward_alpha_candidates import (
    ForwardAlphaError,
    canonical_json,
    implementation_fingerprints,
)
from tradebot.research.market_state_router import MarketStateRouterError, load_forward_snapshots


SCHEMA_VERSION = "2.3-readiness"
REQUIRED_ELIGIBLE_HOURS = 1_440
DISCOVERY_HOURS = 1_104
HOLDOUT_HOURS = 336
FORBIDDEN_PERFORMANCE_KEYS = {
    "return",
    "returns",
    "pnl",
    "profit",
    "drawdown",
    "sharpe",
    "benchmark_return",
    "compounded_return",
}


class ForwardAlphaReadinessError(RuntimeError):
    """Raised when v2.3 decision evidence is malformed, unsafe, or unverifiable."""


@dataclass(frozen=True)
class DecisionRecord:
    hour: datetime
    report_sha256: str
    source_path: str
    snapshot_refs: tuple[tuple[str, str, datetime], ...]
    selected_assets: tuple[str, ...]
    selected_families: tuple[str, ...]


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ForwardAlphaReadinessError(f"{field} must be a non-empty ISO timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ForwardAlphaReadinessError(f"{field} is not valid ISO-8601: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if parsed.minute or parsed.second or parsed.microsecond:
        raise ForwardAlphaReadinessError(f"{field} must be aligned to a UTC hour")
    return parsed


def _validate_report_hash(payload: dict[str, Any]) -> str:
    expected = payload.get("report_sha256")
    unhashed = dict(payload)
    unhashed.pop("report_sha256", None)
    actual = hashlib.sha256(canonical_json(unhashed).encode("utf-8")).hexdigest()
    if not isinstance(expected, str) or expected != actual:
        raise ForwardAlphaReadinessError("decision report_sha256 does not match canonical content")
    return expected


def _load_manifest(path: Path, decision_path: Path, report_hash: str, hour: datetime) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ForwardAlphaReadinessError(f"unreadable manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise ForwardAlphaReadinessError("manifest root is not an object")
    if payload.get("schema_version") != "2.3-decision-manifest":
        raise ForwardAlphaReadinessError("unsupported decision manifest schema")
    if payload.get("paper_only") is not True or payload.get("authorizes_trading") is not False:
        raise ForwardAlphaReadinessError("unsafe decision manifest flags")
    if payload.get("decision_file") != decision_path.name:
        raise ForwardAlphaReadinessError("manifest decision_file does not match decision filename")
    expected_file_hash = hashlib.sha256(decision_path.read_bytes()).hexdigest()
    if payload.get("decision_file_sha256") != expected_file_hash:
        raise ForwardAlphaReadinessError("manifest decision file hash mismatch")
    if payload.get("decision_report_sha256") != report_hash:
        raise ForwardAlphaReadinessError("manifest decision report hash mismatch")
    if payload.get("forward_data_branch") != "forward-data/v2":
        raise ForwardAlphaReadinessError("manifest does not reference forward-data/v2")
    if not isinstance(payload.get("forward_data_head"), str) or not payload["forward_data_head"]:
        raise ForwardAlphaReadinessError("manifest forward_data_head is missing")
    manifest_hour = _parse_utc(payload.get("decision_hour_utc"), "manifest.decision_hour_utc")
    if manifest_hour != hour:
        raise ForwardAlphaReadinessError("manifest decision hour does not match report cutoff")


def _load_decisions(
    decision_folder: str | Path,
    manifest_folder: str | Path,
) -> tuple[list[DecisionRecord], list[dict[str, str]]]:
    decisions_root = Path(decision_folder)
    manifests_root = Path(manifest_folder)
    if not decisions_root.is_dir():
        raise ForwardAlphaReadinessError(f"decision folder does not exist: {decisions_root}")
    if not manifests_root.is_dir():
        raise ForwardAlphaReadinessError(f"manifest folder does not exist: {manifests_root}")

    frozen_fingerprints = implementation_fingerprints()
    records: list[DecisionRecord] = []
    exclusions: list[dict[str, str]] = []
    seen_hashes: set[str] = set()

    for path in sorted(decisions_root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ForwardAlphaReadinessError("decision root is not an object")
            forbidden = FORBIDDEN_PERFORMANCE_KEYS.intersection(payload)
            if forbidden:
                raise ForwardAlphaReadinessError(
                    "decision contains forbidden performance fields: " + ",".join(sorted(forbidden))
                )
            report_hash = _validate_report_hash(payload)
            if report_hash in seen_hashes:
                raise ForwardAlphaReadinessError("duplicate decision report hash")
            seen_hashes.add(report_hash)
            if payload.get("schema_version") != "2.3":
                raise ForwardAlphaReadinessError("unsupported decision schema")
            if payload.get("paper_only") is not True or payload.get("authorizes_trading") is not False:
                raise ForwardAlphaReadinessError("unsafe decision flags")
            if payload.get("fingerprints") != frozen_fingerprints:
                raise ForwardAlphaReadinessError("decision fingerprints do not match frozen v2.3")
            if payload.get("missing_required_hours"):
                raise ForwardAlphaReadinessError("decision lacks contiguous 169-hour input history")
            if int(payload.get("input_snapshot_count", 0)) != 169:
                raise ForwardAlphaReadinessError("decision must reference exactly 169 snapshots")

            hour = _parse_utc(payload.get("data_cutoff_utc"), "data_cutoff_utc")
            raw_refs = payload.get("input_snapshots")
            if not isinstance(raw_refs, list) or len(raw_refs) != 169:
                raise ForwardAlphaReadinessError("input_snapshots must contain exactly 169 references")
            refs: list[tuple[str, str, datetime]] = []
            for index, item in enumerate(raw_refs):
                if not isinstance(item, dict):
                    raise ForwardAlphaReadinessError(f"input_snapshots[{index}] is not an object")
                snapshot_id = item.get("snapshot_id")
                record_sha = item.get("record_sha256")
                if not isinstance(snapshot_id, str) or not snapshot_id:
                    raise ForwardAlphaReadinessError(f"input_snapshots[{index}].snapshot_id is invalid")
                if not isinstance(record_sha, str) or len(record_sha) != 64:
                    raise ForwardAlphaReadinessError(f"input_snapshots[{index}].record_sha256 is invalid")
                refs.append((snapshot_id, record_sha, _parse_utc(item.get("hour"), f"input_snapshots[{index}].hour")))
            if refs[-1][2] != hour:
                raise ForwardAlphaReadinessError("decision cutoff does not equal final snapshot hour")
            if any(refs[index][2] - refs[index - 1][2] != timedelta(hours=1) for index in range(1, len(refs))):
                raise ForwardAlphaReadinessError("decision snapshot references are not contiguous")

            selected = payload.get("selected_candidates")
            weights = payload.get("target_weights")
            if not isinstance(selected, list) or len(selected) > 2:
                raise ForwardAlphaReadinessError("selected_candidates violates the two-asset cap")
            if not isinstance(weights, dict):
                raise ForwardAlphaReadinessError("target_weights is not an object")
            numeric_weights = [float(value) for value in weights.values()]
            if any(value < 0.0 or value > 0.20 + 1e-12 for value in numeric_weights):
                raise ForwardAlphaReadinessError("target weight violates the 20% per-asset cap")
            if sum(numeric_weights) > 0.40 + 1e-12:
                raise ForwardAlphaReadinessError("target weights violate the 40% total cap")
            if float(payload.get("minimum_cash_weight", -1.0)) < 0.60 - 1e-12:
                raise ForwardAlphaReadinessError("decision violates the 60% minimum cash rule")

            assets: list[str] = []
            families: list[str] = []
            for index, item in enumerate(selected):
                if not isinstance(item, dict):
                    raise ForwardAlphaReadinessError(f"selected_candidates[{index}] is not an object")
                asset = item.get("asset")
                family = item.get("family")
                if not isinstance(asset, str) or not asset:
                    raise ForwardAlphaReadinessError(f"selected_candidates[{index}].asset is invalid")
                if not isinstance(family, str) or not family:
                    raise ForwardAlphaReadinessError(f"selected_candidates[{index}].family is invalid")
                assets.append(asset)
                families.append(family)

            manifest_path = manifests_root / path.name
            if not manifest_path.is_file():
                raise ForwardAlphaReadinessError("matching decision manifest is missing")
            _load_manifest(manifest_path, path, report_hash, hour)
            records.append(DecisionRecord(hour, report_hash, path.as_posix(), tuple(refs), tuple(assets), tuple(families)))
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
            ForwardAlphaError,
            ForwardAlphaReadinessError,
        ) as exc:
            exclusions.append({"path": path.as_posix(), "reason": str(exc)})

    by_hour: dict[datetime, DecisionRecord] = {}
    duplicates: set[datetime] = set()
    for record in records:
        if record.hour in by_hour:
            duplicates.add(record.hour)
        else:
            by_hour[record.hour] = record
    for hour in sorted(duplicates):
        exclusions.append({"path": "multiple", "reason": f"duplicate decision hour: {hour.isoformat()}"})
        by_hour.pop(hour, None)
    return [by_hour[hour] for hour in sorted(by_hour)], sorted(exclusions, key=lambda item: (item["path"], item["reason"]))


def _longest_contiguous(records: list[DecisionRecord]) -> list[DecisionRecord]:
    best: list[DecisionRecord] = []
    current: list[DecisionRecord] = []
    for record in records:
        if current and record.hour - current[-1].hour != timedelta(hours=1):
            if len(current) > len(best):
                best = current
            current = []
        current.append(record)
    return current if len(current) > len(best) else best


def evaluate_forward_alpha_readiness(
    decision_folder: str | Path,
    manifest_folder: str | Path,
    snapshot_folder: str | Path,
) -> dict[str, Any]:
    snapshots = load_forward_snapshots(snapshot_folder)
    snapshot_index = {(frame.snapshot_id, frame.record_sha256, frame.hour) for frame in snapshots}
    decisions, exclusions = _load_decisions(decision_folder, manifest_folder)

    verified: list[DecisionRecord] = []
    for decision in decisions:
        missing = [reference for reference in decision.snapshot_refs if reference not in snapshot_index]
        if missing:
            exclusions.append({
                "path": decision.source_path,
                "reason": f"{len(missing)} referenced snapshots failed append-only verification",
            })
        else:
            verified.append(decision)

    contiguous = _longest_contiguous(verified)
    eligible_hours = len(contiguous)
    ready = eligible_hours >= REQUIRED_ELIGIBLE_HOURS
    active = [record for record in contiguous if record.selected_assets]
    asset_counts = Counter(asset for record in active for asset in record.selected_assets)
    family_counts = Counter(family for record in active for family in record.selected_families)
    active_days = sorted({record.hour.date().isoformat() for record in active})
    inventory = [
        {"hour": record.hour.isoformat().replace("+00:00", "Z"), "report_sha256": record.report_sha256}
        for record in contiguous
    ]

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": "READINESS_AND_ACTIVITY_ONLY",
        "status": "READY_FOR_SEALED_OUTCOME_ATTACHMENT" if ready else "INSUFFICIENT_FORWARD_HISTORY",
        "paper_only": True,
        "authorizes_trading": False,
        "performance_calculated": False,
        "performance_fields_disclosed": [],
        "required_eligible_hours": REQUIRED_ELIGIBLE_HOURS,
        "discovery_hours_locked": DISCOVERY_HOURS,
        "holdout_hours_locked": HOLDOUT_HOURS,
        "verified_decision_count": len(verified),
        "longest_contiguous_eligible_hours": eligible_hours,
        "remaining_hours": max(0, REQUIRED_ELIGIBLE_HOURS - eligible_hours),
        "first_eligible_hour_utc": None if not contiguous else contiguous[0].hour.isoformat().replace("+00:00", "Z"),
        "last_eligible_hour_utc": None if not contiguous else contiguous[-1].hour.isoformat().replace("+00:00", "Z"),
        "decision_inventory_sha256": hashlib.sha256(canonical_json(inventory).encode("utf-8")).hexdigest(),
        "snapshot_inventory_count": len(snapshots),
        "excluded_decision_count": len(exclusions),
        "exclusions": sorted(exclusions, key=lambda item: (item["path"], item["reason"])),
        "activity_diagnostics": {
            "active_decision_count": len(active),
            "active_day_count": len(active_days),
            "active_days_utc": active_days,
            "selected_asset_counts": dict(sorted(asset_counts.items())),
            "selected_family_counts": dict(sorted(family_counts.items())),
        },
        "frozen_v23_fingerprints": implementation_fingerprints(),
        "outcome_attachment_unlocked": ready,
        "holdout_unlocked": False,
    }
    report["report_sha256"] = hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
    return report


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify sealed v2.3 forward alpha readiness without calculating returns.")
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--manifests", required=True)
    parser.add_argument("--snapshots", required=True)
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args(argv)
    report = evaluate_forward_alpha_readiness(args.decisions, args.manifests, args.snapshots)
    _atomic_json(Path(args.json_out), report)
    print(json.dumps({
        "status": report["status"],
        "longest_contiguous_eligible_hours": report["longest_contiguous_eligible_hours"],
        "remaining_hours": report["remaining_hours"],
        "activity_diagnostics": report["activity_diagnostics"],
        "performance_calculated": False,
        "authorizes_trading": False,
        "report_sha256": report["report_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
