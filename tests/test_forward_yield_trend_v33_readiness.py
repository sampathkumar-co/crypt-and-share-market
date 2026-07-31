from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import forward_yield_trend_v33 as v33
from tradebot.research import forward_yield_trend_v33_readiness as readiness


def _write_day(
    observations: Path,
    manifests: Path,
    day: datetime,
    action: str,
) -> None:
    observation = {
        "schema_version": v33.SCHEMA_VERSION,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "changes_track_a": False,
        "completed_candle_date_utc": day.date().isoformat(),
        "earliest_eligible_effective_open_utc": (
            day + timedelta(days=2)
        ).isoformat().replace("+00:00", "Z"),
        "historical_promotion_evidence": {
            "corrected_binance_report_sha256": v33.BINANCE_REPORT_SHA256,
            "coinbase_replication_report_sha256": v33.COINBASE_REPORT_SHA256,
        },
        "fingerprints": {
            "scheduled_execution_sha256": v33.SCHEDULED_EXECUTION_SHA256,
        },
        "action": action,
    }
    observation["report_sha256"] = hashlib.sha256(
        canonical_json(observation).encode()
    ).hexdigest()
    observation_path = observations / f"{day.date().isoformat()}.json"
    observation_path.write_text(
        json.dumps(observation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    inventory = [{"key": "source", "sha256": "a" * 64}]
    manifest = {
        "schema_version": v33.MANIFEST_SCHEMA_VERSION,
        "observation_report_sha256": observation["report_sha256"],
        "observation_file_sha256": hashlib.sha256(
            observation_path.read_bytes()
        ).hexdigest(),
        "source_inventory": inventory,
        "source_inventory_sha256": hashlib.sha256(
            canonical_json(inventory).encode()
        ).hexdigest(),
    }
    (manifests / observation_path.name).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _folders(tmp_path: Path) -> tuple[Path, Path]:
    observations = tmp_path / "observations"
    manifests = tmp_path / "manifests"
    observations.mkdir()
    manifests.mkdir()
    return observations, manifests


def test_179_observations_remain_locked(tmp_path: Path) -> None:
    observations, manifests = _folders(tmp_path)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(179):
        _write_day(
            observations,
            manifests,
            start + timedelta(days=index),
            "ENTER" if index == 0 else "HOLD_NO_TRADE",
        )
    report = readiness.assess_readiness(
        observations_folder=observations,
        manifests_folder=manifests,
        latest_available_open_date=start + timedelta(days=300),
    )
    assert report["status"] == readiness.STATUS_HISTORY
    assert report["contiguous_observations"] == 179
    assert report["remaining_observations"] == 1
    assert report["performance_calculated"] is False
    assert report["outcome_attachment_unlocked"] is False


def test_180_days_and_eight_actions_unlock_evaluator_protocol(tmp_path: Path) -> None:
    observations, manifests = _folders(tmp_path)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    action_indices = {0, 20, 40, 60, 80, 100, 120, 140}
    for index in range(180):
        action = "ENTER" if index == 0 else (
            "REBALANCE" if index in action_indices else "HOLD_NO_TRADE"
        )
        _write_day(observations, manifests, start + timedelta(days=index), action)
    report = readiness.assess_readiness(
        observations_folder=observations,
        manifests_folder=manifests,
        latest_available_open_date=start + timedelta(days=300),
    )
    assert report["status"] == readiness.STATUS_READY
    assert report["contiguous_observations"] == 180
    assert report["target_changing_actions"] == 8
    assert report["qualified_entries"] == 1
    assert report["performance_calculated"] is False
    assert report["outcome_attachment_unlocked"] is True


def test_gap_reset_discards_prior_segment(tmp_path: Path) -> None:
    observations, manifests = _folders(tmp_path)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(50):
        action = "GAP_RESET_NO_TRADE" if index == 40 else "HOLD_NO_TRADE"
        _write_day(observations, manifests, start + timedelta(days=index), action)
    report = readiness.assess_readiness(
        observations_folder=observations,
        manifests_folder=manifests,
        latest_available_open_date=start + timedelta(days=100),
    )
    assert report["status"] == readiness.STATUS_HISTORY
    assert report["contiguous_observations"] == 9
    assert report["segment_first_date"] == "2026-02-11"


def test_future_open_keeps_ready_history_locked(tmp_path: Path) -> None:
    observations, manifests = _folders(tmp_path)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    action_indices = {0, 20, 40, 60, 80, 100, 120, 140}
    for index in range(180):
        action = "ENTER" if index == 0 else (
            "REBALANCE" if index in action_indices else "HOLD_NO_TRADE"
        )
        _write_day(observations, manifests, start + timedelta(days=index), action)
    report = readiness.assess_readiness(
        observations_folder=observations,
        manifests_folder=manifests,
        latest_available_open_date=start + timedelta(days=179),
    )
    assert report["status"] == readiness.STATUS_FUTURE
    assert report["outcome_attachment_unlocked"] is False
