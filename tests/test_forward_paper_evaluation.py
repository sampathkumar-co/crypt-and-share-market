from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tradebot.research.forward_paper_evaluation import (
    ASSETS,
    BASE_FRICTION,
    ROUTER_PROTOCOL_SHA256,
    ROUTER_SOURCE_SHA256,
    ActivationLock,
    DecisionPoint,
    EvaluationConfig,
    FileEvidence,
    ForwardEvidenceStore,
    ForwardPaperEvaluationError,
    SnapshotPoint,
    _activation_payload,
    canonical_json,
    evaluate_forward_paper,
    simulate_equal_weight_benchmark,
    simulate_router_block,
)


START = datetime(2026, 8, 1, tzinfo=timezone.utc)
HEAD = "a" * 40


def _canonical_hash(payload: dict) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _snapshot_payload(hour: datetime, *, prices: dict[str, float] | None = None) -> dict:
    captured = hour + timedelta(minutes=17)
    price_map = {asset: 100.0 for asset in ASSETS}
    price_map.update(prices or {})
    assets = {
        asset: {
            "spot_quote": {"available": True, "mid": price_map[asset]},
        }
        for asset in ASSETS
    }
    payload = {
        "schema_version": "2.0",
        "paper_only": True,
        "authorizes_trading": False,
        "snapshot_id": captured.strftime("%Y%m%dT%H%M%S.%fZ"),
        "captured_at_utc": captured.isoformat().replace("+00:00", "Z"),
        "hour_bucket_utc": hour.isoformat().replace("+00:00", "Z"),
        "assets": assets,
        "global": {"available": False},
        "liquidation_events": {"available": False, "events": []},
        "source_errors": {},
    }
    payload["record_sha256"] = _canonical_hash(payload)
    return payload


def _write_snapshot(root: Path, hour: datetime, *, prices=None, suffix: str = "") -> Path:
    payload = _snapshot_payload(hour, prices=prices)
    path = root / "data/forward-market-state/normalized" / f"{payload['snapshot_id']}{suffix}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _snapshot_path_for_hour(root: Path, hour: datetime) -> Path:
    prefix = hour.strftime("%Y%m%dT%H")
    matches = sorted((root / "data/forward-market-state/normalized").glob(f"{prefix}*.json"))
    assert len(matches) == 1, (hour, matches)
    return matches[0]


def _write_decision(
    root: Path,
    hour: datetime,
    *,
    target_weights: dict[str, float] | None = None,
    sleeve: str = "spot_led_continuation",
    fingerprints: dict[str, str] | None = None,
) -> Path:
    weights = target_weights or {}
    input_items = []
    inventory_items = []
    for offset in range(168, -1, -1):
        snapshot_hour = hour - timedelta(hours=offset)
        path = _snapshot_path_for_hour(root, snapshot_hour)
        payload = json.loads(path.read_text(encoding="utf-8"))
        input_items.append({
            "snapshot_id": payload["snapshot_id"],
            "record_sha256": payload["record_sha256"],
            "hour": payload["hour_bucket_utc"],
        })
        inventory_items.append({
            "snapshot_file": path.name,
            "snapshot_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    candidates = [
        {"asset": asset, "sleeve": sleeve, "score": 1.0, "target_weight": weight}
        for asset, weight in sorted(weights.items())
    ]
    report = {
        "schema_version": "2.1",
        "paper_only": True,
        "authorizes_trading": False,
        "candidate_state": "RESEARCH_CANDIDATES" if weights else "CASH",
        "decision_reason": "test",
        "data_cutoff_utc": hour.isoformat().replace("+00:00", "Z"),
        "intended_next_cycle_utc": (hour + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "input_snapshot_count": len(input_items),
        "input_snapshots": input_items,
        "selected_candidates": candidates,
        "target_weights": weights,
        "minimum_cash_weight": 1.0 - sum(weights.values()),
        "fingerprints": fingerprints or {
            "source_sha256": ROUTER_SOURCE_SHA256,
            "protocol_sha256": ROUTER_PROTOCOL_SHA256,
        },
    }
    report["report_sha256"] = _canonical_hash(report)
    stem = hour.strftime("%Y%m%dT%H0000Z")
    decision_path = root / "data/market-state-router/decisions" / f"{stem}.json"
    inventory_path = root / "data/market-state-router/inventories" / f"{stem}.json"
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    inventory = {
        "forward_data_branch": "forward-data/v2",
        "forward_data_head": HEAD,
        "snapshot_files": len(inventory_items),
        "snapshots": inventory_items,
    }
    inventory_path.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return decision_path


def _build_store(
    root: Path,
    *,
    decision_count: int,
    holdout_count: int = 0,
    rising: bool = False,
) -> datetime:
    activation = START + timedelta(hours=168)
    total_decisions = decision_count + holdout_count
    final_snapshot_index = 168 + total_decisions + 1
    for index in range(final_snapshot_index + 1):
        prices = None
        if rising and index >= 169:
            prices = {"BTC": 100.0 + 20.0 * (index - 168)}
        _write_snapshot(root, START + timedelta(hours=index), prices=prices)
    for index in range(total_decisions):
        _write_decision(
            root,
            activation + timedelta(hours=index),
            target_weights={"BTC": 0.25} if rising else {},
        )
    return activation


def _small_config(**changes) -> EvaluationConfig:
    values = {
        "discovery_intervals": 4,
        "half_intervals": 2,
        "holdout_intervals": 2,
        "max_drawdown": 0.12,
        "min_active_hours": 1,
        "min_entry_events": 1,
        "min_positive_asset_omissions": 0,
        "min_positive_sleeve_omissions": 0,
        "max_sleeve_gain_share": 1.0,
    }
    values.update(changes)
    return EvaluationConfig(**values)


def _forbidden_partial_keys(payload) -> set[str]:
    forbidden = {
        "deployable_return",
        "economic_return",
        "worst_drawdown",
        "ledger",
        "entry_events",
        "active_hours",
        "benchmark",
        "sleeve_positive_gains",
    }
    found = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in forbidden:
                found.add(key)
            found.update(_forbidden_partial_keys(value))
    elif isinstance(payload, list):
        for value in payload:
            found.update(_forbidden_partial_keys(value))
    return found


def test_waiting_for_activation_exposes_no_partial_performance(tmp_path):
    _write_snapshot(tmp_path, START)
    report, activation = evaluate_forward_paper(
        tmp_path,
        forward_data_head=HEAD,
        config=_small_config(),
    )
    assert activation is None
    assert report["status"] == "WAITING_FOR_ACTIVATION"
    assert report["authorizes_trading"] is False
    assert report["authorizes_shadow_paper"] is False
    assert _forbidden_partial_keys(report) == set()


def test_new_activation_is_emitted_before_incomplete_discovery(tmp_path):
    activation_hour = _build_store(tmp_path, decision_count=1)
    report, activation = evaluate_forward_paper(
        tmp_path,
        forward_data_head=HEAD,
        config=_small_config(),
    )
    assert report["status"] == "COLLECTING_DISCOVERY"
    assert report["available_contiguous_intervals"] == 1
    assert activation is not None
    assert activation["activation_decision_hour_utc"] == activation_hour.isoformat().replace("+00:00", "Z")
    assert _forbidden_partial_keys(report) == set()


def test_existing_activation_lock_is_hash_verified(tmp_path):
    _build_store(tmp_path, decision_count=1)
    _, activation = evaluate_forward_paper(
        tmp_path,
        forward_data_head=HEAD,
        config=_small_config(),
    )
    assert activation is not None
    path = tmp_path / "data/forward-paper-v22/activation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(activation, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report, repeated = evaluate_forward_paper(
        tmp_path,
        forward_data_head=HEAD,
        config=_small_config(),
    )
    assert repeated is None
    assert report["activation_locked"] is True


def test_tampered_activation_lock_fails_closed(tmp_path):
    _build_store(tmp_path, decision_count=1)
    _, activation = evaluate_forward_paper(
        tmp_path,
        forward_data_head=HEAD,
        config=_small_config(),
    )
    assert activation is not None
    activation["activation_fill_hour_utc"] = START.isoformat().replace("+00:00", "Z")
    path = tmp_path / "data/forward-paper-v22/activation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(activation), encoding="utf-8")
    with pytest.raises(ForwardPaperEvaluationError, match="Activation lock hash mismatch"):
        evaluate_forward_paper(tmp_path, forward_data_head=HEAD, config=_small_config())


def test_nonidentical_duplicate_snapshot_hour_fails_closed(tmp_path):
    _write_snapshot(tmp_path, START)
    _write_snapshot(tmp_path, START, prices={"BTC": 101.0}, suffix="-duplicate")
    with pytest.raises(ForwardPaperEvaluationError, match="Non-identical duplicate snapshot"):
        ForwardEvidenceStore(tmp_path)


def test_router_fingerprint_mismatch_fails_before_scoring(tmp_path):
    activation = START + timedelta(hours=168)
    for index in range(170):
        _write_snapshot(tmp_path, START + timedelta(hours=index))
    _write_decision(
        tmp_path,
        activation,
        fingerprints={
            "source_sha256": "0" * 64,
            "protocol_sha256": ROUTER_PROTOCOL_SHA256,
        },
    )
    with pytest.raises(ForwardPaperEvaluationError, match="Router source fingerprint mismatch"):
        evaluate_forward_paper(tmp_path, forward_data_head=HEAD, config=_small_config())


def test_failed_discovery_does_not_load_or_summarize_holdout(tmp_path):
    activation = _build_store(tmp_path, decision_count=4, rising=False)
    malformed_hour = activation + timedelta(hours=6)
    captured = malformed_hour + timedelta(minutes=17)
    malformed = (
        tmp_path
        / "data/forward-market-state/normalized"
        / f"{captured.strftime('%Y%m%dT%H%M%S.%fZ')}.json"
    )
    malformed.write_text("not-json", encoding="utf-8")
    report, _ = evaluate_forward_paper(
        tmp_path,
        forward_data_head=HEAD,
        config=_small_config(),
    )
    assert report["status"] == "DISCOVERY_REJECTED"
    assert report["holdout_accessed"] is False
    assert "holdout" not in report
    assert report["discovery"]["base"]["deployable_return"] == pytest.approx(0.0)


def test_profitable_discovery_waits_for_complete_holdout(tmp_path):
    _build_store(tmp_path, decision_count=4, rising=True)
    report, _ = evaluate_forward_paper(
        tmp_path,
        forward_data_head=HEAD,
        config=_small_config(),
    )
    assert report["discovery"]["passed"] is True
    assert report["status"] == "DISCOVERY_PASSED_COLLECTING_HOLDOUT"
    assert report["holdout_accessed"] is False
    assert report["eligible_for_shadow_paper_review"] is False


def test_passing_holdout_only_marks_review_eligibility(tmp_path):
    activation = _build_store(tmp_path, decision_count=4, holdout_count=4, rising=True)
    _write_decision(tmp_path, activation + timedelta(hours=5), target_weights={})
    config = _small_config(holdout_intervals=4)
    report, _ = evaluate_forward_paper(
        tmp_path,
        forward_data_head=HEAD,
        config=config,
    )
    assert report["discovery"]["passed"] is True
    assert report["holdout_accessed"] is True
    assert report["holdout"]["passed"] is True
    assert report["status"] == "HOLDOUT_PASSED"
    assert report["eligible_for_shadow_paper_review"] is True
    assert report["authorizes_shadow_paper"] is False
    assert report["authorizes_trading"] is False


def _direct_decision(hour: datetime, weights: dict[str, float]) -> DecisionPoint:
    sleeves = {asset: "spot_led_continuation" for asset in weights}
    return DecisionPoint(
        hour=hour,
        report_sha256="r" * 64,
        target_weights=weights,
        sleeves=sleeves,
        input_snapshot_count=169,
        input_snapshots=tuple(),
        file=FileEvidence("decision.json", "d" * 64),
        inventory_file=FileEvidence("inventory.json", "i" * 64),
        inventory={"snapshots": []},
    )


def _direct_snapshot(hour: datetime, btc: float, others: float = 100.0) -> SnapshotPoint:
    mids = {asset: others for asset in ASSETS}
    mids["BTC"] = btc
    return SnapshotPoint(
        hour=hour,
        snapshot_id=hour.strftime("%Y%m%dT%H0000Z"),
        record_sha256="s" * 64,
        mids=mids,
        file=FileEvidence(f"{hour:%Y%m%dT%H}.json", "f" * 64),
    )


def test_positive_lot_gain_charges_tax_and_tracks_tds():
    decision = _direct_decision(START, {"BTC": 0.25})
    snapshots = {
        START + timedelta(hours=1): _direct_snapshot(START + timedelta(hours=1), 100.0),
        START + timedelta(hours=2): _direct_snapshot(START + timedelta(hours=2), 200.0),
    }
    result = simulate_router_block([decision], snapshots)
    assert result.deployable_return > 0
    assert result.tax_paid > 0
    assert result.tds_receivable > 0
    assert result.economic_return > result.deployable_return
    assert result.entry_events == 1


def test_losing_disposal_receives_no_tax_credit():
    decision = _direct_decision(START, {"BTC": 0.25})
    snapshots = {
        START + timedelta(hours=1): _direct_snapshot(START + timedelta(hours=1), 100.0),
        START + timedelta(hours=2): _direct_snapshot(START + timedelta(hours=2), 50.0),
    }
    result = simulate_router_block([decision], snapshots)
    assert result.deployable_return < 0
    assert result.tax_paid == pytest.approx(0.0)
    assert result.tds_receivable > 0


def test_omitted_asset_weight_remains_cash():
    decision = _direct_decision(START, {"BTC": 0.25})
    snapshots = {
        START + timedelta(hours=1): _direct_snapshot(START + timedelta(hours=1), 100.0),
        START + timedelta(hours=2): _direct_snapshot(START + timedelta(hours=2), 200.0),
    }
    result = simulate_router_block([decision], snapshots, omitted_asset="BTC")
    assert result.deployable_return == pytest.approx(0.0)
    assert result.economic_return == pytest.approx(0.0)
    assert result.transaction_count == 0
    assert result.active_hours == 0


def test_equal_weight_budget_includes_buy_friction():
    snapshots = {
        START + timedelta(hours=1): _direct_snapshot(START + timedelta(hours=1), 100.0),
        START + timedelta(hours=2): _direct_snapshot(START + timedelta(hours=2), 100.0),
    }
    result = simulate_equal_weight_benchmark(
        snapshots,
        fill_hour=START + timedelta(hours=1),
        final_mark_hour=START + timedelta(hours=2),
        friction=BASE_FRICTION,
    )
    assert result.ending_deployable_value >= 0
    assert result.deployable_return < 0
    assert result.economic_return > result.deployable_return
    assert result.tax_paid == pytest.approx(0.0)


def test_future_files_do_not_change_prior_discovery_report(tmp_path):
    activation = _build_store(tmp_path, decision_count=4, rising=True)
    first, _ = evaluate_forward_paper(
        tmp_path,
        forward_data_head=HEAD,
        config=_small_config(),
    )
    _write_snapshot(tmp_path, activation + timedelta(hours=100), prices={"BTC": 1.0})
    second, _ = evaluate_forward_paper(
        tmp_path,
        forward_data_head=HEAD,
        config=_small_config(),
    )
    assert second == first


def test_snapshot_file_change_breaks_decision_inventory(tmp_path):
    activation = _build_store(tmp_path, decision_count=1)
    path = _snapshot_path_for_hour(tmp_path, activation)
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ForwardPaperEvaluationError, match="inventory file hash mismatch"):
        evaluate_forward_paper(tmp_path, forward_data_head=HEAD, config=_small_config())


def test_complete_report_hash_is_canonical(tmp_path):
    _build_store(tmp_path, decision_count=4, rising=False)
    report, _ = evaluate_forward_paper(
        tmp_path,
        forward_data_head=HEAD,
        config=_small_config(),
    )
    expected = report.pop("report_sha256")
    assert hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest() == expected
