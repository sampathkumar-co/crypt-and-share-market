from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import forward_yield_trend_v33 as v33
from tradebot.research import forward_yield_trend_v33_sources as sources

SCHEMA_VERSION = "3.3-forward-observation-readiness"
MIN_OBSERVATIONS = 180
MIN_TARGET_ACTIONS = 8
STATUS_HISTORY = "INSUFFICIENT_FORWARD_OBSERVATIONS"
STATUS_ACTIVITY = "INSUFFICIENT_TARGET_ACTIVITY"
STATUS_FUTURE = "INSUFFICIENT_REQUIRED_FUTURE_OPENS"
STATUS_READY = "READY_FOR_PREREGISTERED_FORWARD_EVALUATOR"


class ForwardReadinessV33Error(RuntimeError):
    pass


def _parse_day(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def _verify_observation(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if report["schema_version"] != v33.SCHEMA_VERSION:
        raise ForwardReadinessV33Error(f"invalid schema: {path.name}")
    for key in ("paper_only",):
        if report[key] is not True:
            raise ForwardReadinessV33Error(f"invalid {key}: {path.name}")
    for key in ("authorizes_trading", "authorizes_shadow_paper", "changes_track_a"):
        if report[key] is not False:
            raise ForwardReadinessV33Error(f"invalid {key}: {path.name}")
    canonical = dict(report)
    expected = canonical.pop("report_sha256")
    actual = hashlib.sha256(canonical_json(canonical).encode()).hexdigest()
    if actual != expected:
        raise ForwardReadinessV33Error(f"report hash mismatch: {path.name}")
    if report["historical_promotion_evidence"] != {
        "corrected_binance_report_sha256": v33.BINANCE_REPORT_SHA256,
        "coinbase_replication_report_sha256": v33.COINBASE_REPORT_SHA256,
    }:
        raise ForwardReadinessV33Error(
            f"promotion evidence changed: {path.name}"
        )
    if report["fingerprints"]["scheduled_execution_sha256"] != (
        v33.SCHEDULED_EXECUTION_SHA256
    ):
        raise ForwardReadinessV33Error(
            f"execution fingerprint changed: {path.name}"
        )
    if report["action"] not in v33.VALID_ACTIONS:
        raise ForwardReadinessV33Error(f"invalid action: {path.name}")
    return report


def _verify_manifest(
    path: Path,
    observation_path: Path,
    report: dict[str, Any],
) -> None:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest["schema_version"] != v33.MANIFEST_SCHEMA_VERSION:
        raise ForwardReadinessV33Error(f"invalid manifest: {path.name}")
    if manifest["observation_report_sha256"] != report["report_sha256"]:
        raise ForwardReadinessV33Error(f"manifest report mismatch: {path.name}")
    if manifest["observation_file_sha256"] != hashlib.sha256(
        observation_path.read_bytes()
    ).hexdigest():
        raise ForwardReadinessV33Error(f"manifest file mismatch: {path.name}")
    inventory = manifest["source_inventory"]
    actual = hashlib.sha256(canonical_json(inventory).encode()).hexdigest()
    if actual != manifest["source_inventory_sha256"]:
        raise ForwardReadinessV33Error(
            f"manifest inventory mismatch: {path.name}"
        )


def assess_readiness(
    *,
    observations_folder: Path,
    manifests_folder: Path,
    latest_available_open_date: datetime,
) -> dict[str, Any]:
    observations: list[tuple[datetime, dict[str, Any], Path]] = []
    for path in sorted(observations_folder.glob("*.json")):
        report = _verify_observation(path)
        day = _parse_day(report["completed_candle_date_utc"])
        if path.stem != day.date().isoformat():
            raise ForwardReadinessV33Error(f"filename/date mismatch: {path.name}")
        manifest_path = manifests_folder / path.name
        if not manifest_path.is_file():
            raise ForwardReadinessV33Error(f"missing manifest: {path.name}")
        _verify_manifest(manifest_path, path, report)
        observations.append((day, report, path))

    segment: list[tuple[datetime, dict[str, Any], Path]] = []
    for item in observations:
        day, report, _ = item
        if (
            not segment
            or day == segment[-1][0].replace() + __import__("datetime").timedelta(days=1)
        ):
            segment.append(item)
        else:
            segment = [item]
        if report["action"] == "GAP_RESET_NO_TRADE":
            segment = []

    observation_count = len(segment)
    target_actions = sum(
        report["action"] in v33.TARGET_CHANGING_ACTIONS
        for _, report, _ in segment
    )
    entries = sum(report["action"] == "ENTER" for _, report, _ in segment)
    first_day = segment[0][0].date().isoformat() if segment else None
    last_day = segment[-1][0].date().isoformat() if segment else None
    latest_required_open = (
        datetime.fromisoformat(
            segment[-1][1]["earliest_eligible_effective_open_utc"].replace(
                "Z", "+00:00"
            )
        )
        if segment
        else None
    )

    if observation_count < MIN_OBSERVATIONS:
        status = STATUS_HISTORY
    elif target_actions < MIN_TARGET_ACTIONS or entries < 1:
        status = STATUS_ACTIVITY
    elif latest_required_open is not None and latest_available_open_date < latest_required_open:
        status = STATUS_FUTURE
    else:
        status = STATUS_READY

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "performance_calculated": False,
        "outcome_attachment_unlocked": status == STATUS_READY,
        "status": status,
        "minimum_required_observations": MIN_OBSERVATIONS,
        "minimum_required_target_actions": MIN_TARGET_ACTIONS,
        "contiguous_observations": observation_count,
        "remaining_observations": max(0, MIN_OBSERVATIONS - observation_count),
        "target_changing_actions": target_actions,
        "qualified_entries": entries,
        "segment_first_date": first_day,
        "segment_last_date": last_day,
        "latest_required_effective_open_utc": (
            latest_required_open.isoformat().replace("+00:00", "Z")
            if latest_required_open
            else None
        ),
        "latest_available_open_date_utc": latest_available_open_date.isoformat().replace(
            "+00:00", "Z"
        ),
        "historical_promotion_evidence": {
            "corrected_binance_report_sha256": v33.BINANCE_REPORT_SHA256,
            "coinbase_replication_report_sha256": v33.COINBASE_REPORT_SHA256,
        },
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode()
    ).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assess v3.3 readiness")
    parser.add_argument("--observations-folder", required=True)
    parser.add_argument("--manifests-folder", required=True)
    parser.add_argument("--latest-available-open-date", required=True)
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args(argv)
    latest = datetime.fromisoformat(
        args.latest_available_open_date.replace("Z", "+00:00")
    )
    report = assess_readiness(
        observations_folder=Path(args.observations_folder),
        manifests_folder=Path(args.manifests_folder),
        latest_available_open_date=latest,
    )
    output = Path(args.json_out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
