from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tradebot.research.market_state_router import (
    MarketStateRouterError,
    canonical_json,
    implementation_fingerprints,
    load_forward_snapshots,
)

SCHEMA_VERSION = "2.2-readiness"
REQUIRED_ELIGIBLE_HOURS = 1_440
DISCOVERY_HOURS = 1_104
HOLDOUT_HOURS = 336


class ForwardReadinessError(RuntimeError):
    """Raised when sealed forward-evaluation evidence is malformed or unsafe."""


@dataclass(frozen=True)
class DecisionRecord:
    hour: datetime
    report_sha256: str
    source_path: str
    snapshot_refs: tuple[tuple[str, str, datetime], ...]


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ForwardReadinessError(f"{field} must be a non-empty ISO timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ForwardReadinessError(f"{field} is not valid ISO-8601: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if parsed.minute or parsed.second or parsed.microsecond:
        raise ForwardReadinessError(f"{field} must be aligned to a UTC hour")
    return parsed


def _validate_report_hash(payload: dict[str, Any]) -> str:
    expected = payload.get("report_sha256")
    unhashed = dict(payload)
    unhashed.pop("report_sha256", None)
    actual = hashlib.sha256(canonical_json(unhashed).encode("utf-8")).hexdigest()
    if not isinstance(expected, str) or expected != actual:
        raise ForwardReadinessError("Decision report_sha256 does not match canonical content")
    return expected


def _load_decisions(folder: str | Path) -> tuple[list[DecisionRecord], list[dict[str, str]]]:
    root = Path(folder)
    if not root.exists() or not root.is_dir():
        raise ForwardReadinessError(f"Decision folder does not exist: {root}")

    current_fingerprints = implementation_fingerprints()
    records: list[DecisionRecord] = []
    exclusions: list[dict[str, str]] = []
    seen_report_hashes: set[str] = set()

    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ForwardReadinessError("decision root is not an object")
            report_hash = _validate_report_hash(payload)
            if report_hash in seen_report_hashes:
                raise ForwardReadinessError("duplicate decision report hash")
            seen_report_hashes.add(report_hash)
            if payload.get("schema_version") != "2.1":
                raise ForwardReadinessError("unsupported decision schema")
            if payload.get("paper_only") is not True or payload.get("authorizes_trading") is not False:
                raise ForwardReadinessError("unsafe decision flags")
            if payload.get("fingerprints") != current_fingerprints:
                raise ForwardReadinessError("decision fingerprints do not match frozen v2.1")
            if payload.get("missing_required_hours"):
                raise ForwardReadinessError("decision lacks contiguous 169-hour input history")
            if int(payload.get("input_snapshot_count", 0)) < 169:
                raise ForwardReadinessError("decision references fewer than 169 snapshots")

            hour = _parse_utc(payload.get("data_cutoff_utc"), "data_cutoff_utc")
            raw_refs = payload.get("input_snapshots")
            if not isinstance(raw_refs, list) or len(raw_refs) < 169:
                raise ForwardReadinessError("input_snapshots is incomplete")
            refs: list[tuple[str, str, datetime]] = []
            for index, item in enumerate(raw_refs):
                if not isinstance(item, dict):
                    raise ForwardReadinessError(f"input_snapshots[{index}] is not an object")
                snapshot_id = item.get("snapshot_id")
                record_sha = item.get("record_sha256")
                if not isinstance(snapshot_id, str) or not snapshot_id:
                    raise ForwardReadinessError(f"input_snapshots[{index}].snapshot_id is invalid")
                if not isinstance(record_sha, str) or len(record_sha) != 64:
                    raise ForwardReadinessError(f"input_snapshots[{index}].record_sha256 is invalid")
                refs.append((snapshot_id, record_sha, _parse_utc(item.get("hour"), f"input_snapshots[{index}].hour")))
            if refs[-1][2] != hour:
                raise ForwardReadinessError("decision cutoff does not equal final referenced snapshot hour")
            if any(refs[i][2] - refs[i - 1][2] != timedelta(hours=1) for i in range(1, len(refs))):
                raise ForwardReadinessError("decision snapshot references are not contiguous")
            records.append(DecisionRecord(hour, report_hash, path.as_posix(), tuple(refs)))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, MarketStateRouterError, ForwardReadinessError) as exc:
            exclusions.append({"path": path.as_posix(), "reason": str(exc)})

    by_hour: dict[datetime, DecisionRecord] = {}
    duplicate_hours: set[datetime] = set()
    for record in records:
        if record.hour in by_hour:
            duplicate_hours.add(record.hour)
        else:
            by_hour[record.hour] = record
    if duplicate_hours:
        for hour in sorted(duplicate_hours):
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


def evaluate_readiness(decision_folder: str | Path, snapshot_folder: str | Path) -> dict[str, Any]:
    snapshots = load_forward_snapshots(snapshot_folder)
    snapshot_index = {(frame.snapshot_id, frame.record_sha256, frame.hour) for frame in snapshots}
    decisions, exclusions = _load_decisions(decision_folder)

    verified: list[DecisionRecord] = []
    for decision in decisions:
        missing = [ref for ref in decision.snapshot_refs if ref not in snapshot_index]
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
    status = "READY_FOR_SEALED_OUTCOME_ATTACHMENT" if ready else "INSUFFICIENT_FORWARD_HISTORY"
    first_hour = contiguous[0].hour if contiguous else None
    last_hour = contiguous[-1].hour if contiguous else None

    inventory = [
        {"hour": item.hour.isoformat().replace("+00:00", "Z"), "report_sha256": item.report_sha256}
        for item in contiguous
    ]
    inventory_sha = hashlib.sha256(canonical_json(inventory).encode("utf-8")).hexdigest()
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": "READINESS_ONLY",
        "status": status,
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
        "first_eligible_hour_utc": None if first_hour is None else first_hour.isoformat().replace("+00:00", "Z"),
        "last_eligible_hour_utc": None if last_hour is None else last_hour.isoformat().replace("+00:00", "Z"),
        "decision_inventory_sha256": inventory_sha,
        "snapshot_inventory_count": len(snapshots),
        "excluded_decision_count": len(exclusions),
        "exclusions": sorted(exclusions, key=lambda item: (item["path"], item["reason"])),
        "frozen_v21_fingerprints": implementation_fingerprints(),
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
    parser = argparse.ArgumentParser(description="Run the sealed v2.2 readiness-only forward evaluator.")
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--snapshots", required=True)
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args(argv)
    report = evaluate_readiness(args.decisions, args.snapshots)
    _atomic_json(Path(args.json_out), report)
    print(json.dumps({
        "status": report["status"],
        "longest_contiguous_eligible_hours": report["longest_contiguous_eligible_hours"],
        "remaining_hours": report["remaining_hours"],
        "performance_calculated": False,
        "authorizes_trading": False,
        "report_sha256": report["report_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
