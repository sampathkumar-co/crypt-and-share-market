from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradebot.research import forward_router_readiness as readiness
from tradebot.research.market_state_router import SnapshotFrame


def _hour(offset: int = 0):
    return datetime(2026, 7, 30, tzinfo=timezone.utc) + timedelta(hours=offset)


def _snapshot(offset: int) -> SnapshotFrame:
    return SnapshotFrame(
        hour=_hour(offset),
        captured_at=_hour(offset),
        snapshot_id=f"snapshot-{offset}",
        record_sha256=f"{offset:064x}",
        assets={},
        global_state={},
        source_path=f"snapshot-{offset}.json",
    )


def _decision(offset: int) -> readiness.DecisionRecord:
    frame = _snapshot(offset)
    return readiness.DecisionRecord(
        hour=frame.hour,
        report_sha256=f"{offset + 10_000:064x}",
        source_path=f"decision-{offset}.json",
        snapshot_refs=((frame.snapshot_id, frame.record_sha256, frame.hour),),
    )


def test_readiness_never_discloses_performance_early(monkeypatch):
    snapshots = [_snapshot(index) for index in range(12)]
    decisions = [_decision(index) for index in range(12)]
    monkeypatch.setattr(readiness, "load_forward_snapshots", lambda folder: snapshots)
    monkeypatch.setattr(readiness, "_load_decisions", lambda folder: (decisions, []))
    monkeypatch.setattr(readiness, "implementation_fingerprints", lambda: {"source_sha256": "a", "protocol_sha256": "b"})

    report = readiness.evaluate_readiness("decisions", "snapshots")

    assert report["status"] == "INSUFFICIENT_FORWARD_HISTORY"
    assert report["longest_contiguous_eligible_hours"] == 12
    assert report["remaining_hours"] == 1428
    assert report["performance_calculated"] is False
    assert report["performance_fields_disclosed"] == []
    assert report["outcome_attachment_unlocked"] is False
    assert report["holdout_unlocked"] is False
    assert report["authorizes_trading"] is False
    forbidden = {"return", "pnl", "drawdown", "sharpe", "profit"}
    assert not forbidden.intersection(report)


def test_only_longest_contiguous_verified_run_counts(monkeypatch):
    offsets = [0, 1, 2, 8, 9]
    snapshots = [_snapshot(index) for index in offsets]
    decisions = [_decision(index) for index in offsets]
    monkeypatch.setattr(readiness, "load_forward_snapshots", lambda folder: snapshots)
    monkeypatch.setattr(readiness, "_load_decisions", lambda folder: (decisions, []))
    monkeypatch.setattr(readiness, "implementation_fingerprints", lambda: {})

    report = readiness.evaluate_readiness("decisions", "snapshots")

    assert report["verified_decision_count"] == 5
    assert report["longest_contiguous_eligible_hours"] == 3
    assert report["first_eligible_hour_utc"] == "2026-07-30T00:00:00Z"
    assert report["last_eligible_hour_utc"] == "2026-07-30T02:00:00Z"


def test_unverified_snapshot_reference_is_excluded(monkeypatch):
    snapshots = [_snapshot(0)]
    invalid = readiness.DecisionRecord(
        hour=_hour(0),
        report_sha256="f" * 64,
        source_path="decision.json",
        snapshot_refs=(("other", "e" * 64, _hour(0)),),
    )
    monkeypatch.setattr(readiness, "load_forward_snapshots", lambda folder: snapshots)
    monkeypatch.setattr(readiness, "_load_decisions", lambda folder: ([invalid], []))
    monkeypatch.setattr(readiness, "implementation_fingerprints", lambda: {})

    report = readiness.evaluate_readiness("decisions", "snapshots")

    assert report["verified_decision_count"] == 0
    assert report["longest_contiguous_eligible_hours"] == 0
    assert report["excluded_decision_count"] == 1
    assert "failed append-only verification" in report["exclusions"][0]["reason"]
