from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tradebot.research.forward_alpha_v25_readiness as readiness
from tradebot.research.forward_alpha_v25 import canonical_json, implementation_fingerprints


def _hour(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _write_snapshot(folder: Path, index: int, hour: datetime) -> tuple[str, str, datetime]:
    payload = {
        "schema_version": "2.0",
        "paper_only": True,
        "authorizes_trading": False,
        "snapshot_id": f"snapshot-{index:04d}",
        "captured_at_utc": _hour(hour + timedelta(minutes=1)),
        "hour_bucket_utc": _hour(hour),
        "assets": {},
        "global": {},
    }
    payload["record_sha256"] = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    path = folder / f"snapshot-{index:04d}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload["snapshot_id"], payload["record_sha256"], hour


def _write_decision(
    decisions: Path,
    manifests: Path,
    refs: list[tuple[str, str, datetime]],
    *,
    active: bool,
) -> Path:
    cutoff = refs[-1][2]
    selected = []
    weights = {}
    if active:
        selected = [{
            "asset": "BTC",
            "family": "residual_momentum_microstructure",
            "score": 7.5,
            "amplitude": 0.008,
            "event_key": "a" * 64,
            "target_weight": 0.15,
        }]
        weights = {"BTC": 0.15}
    report = {
        "schema_version": "2.5",
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "candidate_state": "RESEARCH_CANDIDATES" if active else "CASH",
        "decision_reason": "fixture",
        "data_cutoff_utc": _hour(cutoff),
        "intended_next_cycle_utc": _hour(cutoff + timedelta(hours=1)),
        "input_snapshot_count": 169,
        "input_snapshots": [
            {"snapshot_id": snapshot_id, "record_sha256": record_sha, "hour": _hour(hour)}
            for snapshot_id, record_sha, hour in refs
        ],
        "missing_required_hours": [],
        "global_controls": {},
        "asset_diagnostics": {},
        "qualified_candidates": selected,
        "selected_candidates": selected,
        "target_weights": weights,
        "minimum_cash_weight": 0.85 if active else 1.0,
        "correlation_filter": None,
        "fingerprints": implementation_fingerprints(),
    }
    report["report_sha256"] = hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
    name = cutoff.strftime("%Y%m%dT%H0000Z.json")
    path = decisions / name
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "2.5-decision-manifest",
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "decision_hour_utc": _hour(cutoff),
        "decision_file": name,
        "decision_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "decision_report_sha256": report["report_sha256"],
        "forward_data_branch": "forward-data/v2",
        "forward_data_head": "fixture-head",
        "workflow_run_id": "1",
        "workflow_run_attempt": "1",
    }
    (manifests / name).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _fixture(tmp_path: Path, decision_count: int = 1) -> tuple[Path, Path, Path]:
    snapshots = tmp_path / "snapshots"
    decisions = tmp_path / "decisions"
    manifests = tmp_path / "manifests"
    snapshots.mkdir()
    decisions.mkdir()
    manifests.mkdir()
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    refs = [_write_snapshot(snapshots, index, start + timedelta(hours=index)) for index in range(168 + decision_count)]
    for offset in range(decision_count):
        _write_decision(
            decisions,
            manifests,
            refs[offset : offset + 169],
            active=offset % 2 == 0,
        )
    return decisions, manifests, snapshots


def test_readiness_reports_only_inventory_and_activity(tmp_path: Path) -> None:
    decisions, manifests, snapshots = _fixture(tmp_path)

    report = readiness.evaluate_forward_alpha_v25_readiness(decisions, manifests, snapshots)

    assert report["status"] == "INSUFFICIENT_FORWARD_HISTORY"
    assert report["longest_contiguous_eligible_hours"] == 1
    assert report["activity_diagnostics"]["active_decision_count"] == 1
    assert report["activity_diagnostics"]["selected_asset_counts"] == {"BTC": 1}
    assert report["performance_calculated"] is False
    assert report["authorizes_trading"] is False
    assert report["authorizes_shadow_paper"] is False
    assert report["purge_hours_locked"] == 8
    assert report["outcome_attachment_unlocked"] is False
    assert report["holdout_unlocked"] is False
    assert not any(key in report for key in ("return", "pnl", "profit", "drawdown", "sharpe"))


def test_manifest_tampering_excludes_decision(tmp_path: Path) -> None:
    decisions, manifests, snapshots = _fixture(tmp_path)
    manifest_path = next(manifests.glob("*.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["decision_file_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = readiness.evaluate_forward_alpha_v25_readiness(decisions, manifests, snapshots)

    assert report["verified_decision_count"] == 0
    assert report["longest_contiguous_eligible_hours"] == 0
    assert report["excluded_decision_count"] == 1
    assert "manifest decision file hash mismatch" in report["exclusions"][0]["reason"]


def test_ready_unlocks_only_outcome_attachment(monkeypatch, tmp_path: Path) -> None:
    decisions, manifests, snapshots = _fixture(tmp_path, decision_count=2)
    monkeypatch.setattr(readiness, "REQUIRED_ELIGIBLE_HOURS", 2)
    monkeypatch.setattr(readiness, "REQUIRED_FUTURE_HOURS", 0)

    report = readiness.evaluate_forward_alpha_v25_readiness(decisions, manifests, snapshots)

    assert report["status"] == "READY_FOR_SEALED_OUTCOME_ATTACHMENT"
    assert report["longest_contiguous_eligible_hours"] == 2
    assert report["remaining_hours"] == 0
    assert report["outcome_attachment_unlocked"] is True
    assert report["holdout_unlocked"] is False
    assert report["authorizes_trading"] is False
    assert report["authorizes_shadow_paper"] is False


def test_broken_decision_continuity_does_not_count_as_one_run(tmp_path: Path) -> None:
    decisions, manifests, snapshots = _fixture(tmp_path, decision_count=3)
    middle = sorted(decisions.glob("*.json"))[1]
    (manifests / middle.name).unlink()
    middle.unlink()

    report = readiness.evaluate_forward_alpha_v25_readiness(decisions, manifests, snapshots)

    assert report["verified_decision_count"] == 2
    assert report["longest_contiguous_eligible_hours"] == 1


def test_required_exit_snapshots_keep_outcomes_locked(monkeypatch, tmp_path: Path) -> None:
    decisions, manifests, snapshots = _fixture(tmp_path, decision_count=2)
    monkeypatch.setattr(readiness, "REQUIRED_ELIGIBLE_HOURS", 2)

    report = readiness.evaluate_forward_alpha_v25_readiness(decisions, manifests, snapshots)

    assert report["status"] == "INSUFFICIENT_REQUIRED_EXIT_SNAPSHOTS"
    assert report["longest_contiguous_eligible_hours"] == 2
    assert report["remaining_hours"] == 0
    assert len(report["missing_future_snapshot_hours"]) == 9
    assert report["outcome_attachment_unlocked"] is False
    assert report["performance_calculated"] is False


def test_shadow_authorization_flag_excludes_decision(tmp_path: Path) -> None:
    decisions, manifests, snapshots = _fixture(tmp_path)
    decision_path = next(decisions.glob("*.json"))
    manifest_path = manifests / decision_path.name
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    decision["authorizes_shadow_paper"] = True
    decision.pop("report_sha256")
    decision["report_sha256"] = hashlib.sha256(canonical_json(decision).encode("utf-8")).hexdigest()
    decision_path.write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["decision_file_sha256"] = hashlib.sha256(decision_path.read_bytes()).hexdigest()
    manifest["decision_report_sha256"] = decision["report_sha256"]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = readiness.evaluate_forward_alpha_v25_readiness(decisions, manifests, snapshots)

    assert report["verified_decision_count"] == 0
    assert report["excluded_decision_count"] == 1
    assert "unsafe decision flags" in report["exclusions"][0]["reason"]
