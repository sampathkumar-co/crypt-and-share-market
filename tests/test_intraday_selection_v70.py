from __future__ import annotations

from dataclasses import replace

import pytest

from tradebot.research.intraday_alpha_lab_v70 import CandidateEvidence, CandidateFamily, evidence_fingerprint
from tradebot.research.intraday_selection_v70 import (
    TrialLedgerEntry,
    authorize_single_sealed_holdout_release,
    build_pre_holdout_manifest,
    commit_sealed_holdout_before_fitting,
    holdout_commitment_fingerprint,
    holdout_release_fingerprint,
    manifest_fingerprint,
    verify_holdout_commitment_is_pre_fit,
    verify_holdout_release_is_non_authorizing,
    verify_manifest_is_non_authorizing,
)


PROTOCOL = "frozen v7 protocol\n"


def commitment():
    return commit_sealed_holdout_before_fitting(
        PROTOCOL,
        sealed_holdout_id="v70-holdout-001",
        sealed_holdout_fingerprint="a" * 64,
    )


def candidate(candidate_id: str, family: CandidateFamily, stress: float, trials: int = 4) -> CandidateEvidence:
    return CandidateEvidence(
        candidate_id=candidate_id,
        family=family,
        standard_fold_excess=(0.01,) * 8,
        stress_fold_excess=(0.005,) * 8,
        standard_compounded_excess=0.10,
        stress_compounded_excess=stress,
        first_half_excess=0.02,
        second_half_excess=0.03,
        delayed_stress_excess=0.01,
        best_trade_removed_stress_excess=0.02,
        best_month_removed_stress_excess=0.01,
        maximum_drawdown=0.03,
        maximum_positive_trade_share=0.10,
        maximum_positive_month_share=0.20,
        target_changing_actions=80,
        dsr_probability=0.97,
        pbo=0.10,
        minimum_track_record_satisfied=True,
        independent_source_replication_passed=True,
        trial_count=trials,
    )


def tournament() -> tuple[CandidateEvidence, ...]:
    return (
        candidate("trend", CandidateFamily.HOURLY_TREND, 0.04),
        candidate("reversal", CandidateFamily.SHOCK_REVERSAL, 0.03),
        candidate("breakout", CandidateFamily.VOLATILITY_BREAKOUT, 0.06),
        candidate("relative", CandidateFamily.RELATIVE_STRENGTH, 0.05),
    )


def ledger(items: tuple[CandidateEvidence, ...]) -> tuple[TrialLedgerEntry, ...]:
    return tuple(
        TrialLedgerEntry(item.candidate_id, item.family, index, evidence_fingerprint(item))
        for index, item in enumerate(items, start=1)
    )


def manifest_for(items: tuple[CandidateEvidence, ...]):
    return build_pre_holdout_manifest(
        PROTOCOL,
        items,
        ledger(items),
        holdout_commitment=commitment(),
    )


def test_holdout_is_committed_before_candidate_evidence_is_selected() -> None:
    frozen = commitment()
    assert frozen.sealed_holdout_id == "v70-holdout-001"
    assert frozen.opened is False
    assert frozen.committed_before_fitting is True
    assert len(holdout_commitment_fingerprint(frozen)) == 64
    verify_holdout_commitment_is_pre_fit(frozen)


def test_selects_best_gate_survivor_before_holdout() -> None:
    items = tournament()
    manifest = manifest_for(items)
    assert manifest.selected_candidate_id == "breakout"
    assert manifest.ranked_candidate_ids == ("breakout", "relative", "trend", "reversal")
    assert manifest.holdout_opened is False
    assert manifest.sealed_holdout_id == "v70-holdout-001"
    verify_manifest_is_non_authorizing(manifest)


def test_failed_candidate_is_recorded_not_silently_removed() -> None:
    items = list(tournament())
    items[2] = replace(items[2], dsr_probability=0.2)
    frozen = tuple(items)
    manifest = manifest_for(frozen)
    assert "breakout" not in manifest.ranked_candidate_ids
    assert "deflated_sharpe_failed" in manifest.rejected["breakout"]


def test_rejects_holdout_contact_and_trial_reset() -> None:
    items = tournament()
    with pytest.raises(ValueError, match="before opening"):
        build_pre_holdout_manifest(
            PROTOCOL,
            items,
            ledger(items),
            holdout_commitment=commitment(),
            holdout_opened=True,
        )
    reset = tuple(replace(item, trial_count=1) for item in items)
    with pytest.raises(ValueError, match="ledger size"):
        build_pre_holdout_manifest(
            PROTOCOL,
            reset,
            ledger(reset),
            holdout_commitment=commitment(),
        )


def test_rejects_missing_frozen_family_and_fingerprint_tampering() -> None:
    items = tournament()
    with pytest.raises(ValueError, match="exactly the four"):
        build_pre_holdout_manifest(
            PROTOCOL,
            items[:-1],
            ledger(items[:-1]),
            holdout_commitment=commitment(),
        )
    bad = list(ledger(items))
    bad[0] = replace(bad[0], evidence_fingerprint="0" * 64)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        build_pre_holdout_manifest(
            PROTOCOL,
            items,
            tuple(bad),
            holdout_commitment=commitment(),
        )


def test_rejects_late_or_mismatched_holdout_commitment() -> None:
    items = tournament()
    with pytest.raises(ValueError, match="frozen and unopened"):
        build_pre_holdout_manifest(
            PROTOCOL,
            items,
            ledger(items),
            holdout_commitment=replace(commitment(), opened=True),
        )
    wrong_protocol = commit_sealed_holdout_before_fitting(
        "different protocol\n",
        sealed_holdout_id="v70-holdout-001",
        sealed_holdout_fingerprint="a" * 64,
    )
    with pytest.raises(ValueError, match="does not match the frozen protocol"):
        build_pre_holdout_manifest(
            PROTOCOL,
            items,
            ledger(items),
            holdout_commitment=wrong_protocol,
        )


def test_manifest_fingerprint_is_deterministic() -> None:
    manifest = manifest_for(tournament())
    assert manifest_fingerprint(manifest) == manifest_fingerprint(manifest)


def test_authorizes_exactly_one_paper_only_precommitted_holdout_release() -> None:
    frozen = commitment()
    manifest = manifest_for(tournament())
    release = authorize_single_sealed_holdout_release(
        manifest,
        expected_manifest_fingerprint=manifest_fingerprint(manifest),
        holdout_commitment=frozen,
    )
    assert release.selected_candidate_id == "breakout"
    assert release.evaluation_count == 1
    assert release.sealed_holdout_id == frozen.sealed_holdout_id
    assert release.consumed_holdout_ids == ("v70-holdout-001",)
    verify_holdout_release_is_non_authorizing(release)
    assert holdout_release_fingerprint(release) == holdout_release_fingerprint(release)


def test_holdout_release_rejects_manifest_drift_and_reuse() -> None:
    frozen = commitment()
    manifest = manifest_for(tournament())
    with pytest.raises(ValueError, match="manifest fingerprint mismatch"):
        authorize_single_sealed_holdout_release(
            manifest,
            expected_manifest_fingerprint="0" * 64,
            holdout_commitment=frozen,
        )
    with pytest.raises(ValueError, match="never be reused"):
        authorize_single_sealed_holdout_release(
            manifest,
            expected_manifest_fingerprint=manifest_fingerprint(manifest),
            holdout_commitment=frozen,
            consumed_holdout_ids=("v70-holdout-001",),
        )


def test_holdout_release_rejects_commitment_substitution() -> None:
    manifest = manifest_for(tournament())
    substituted = commit_sealed_holdout_before_fitting(
        PROTOCOL,
        sealed_holdout_id="v70-holdout-002",
        sealed_holdout_fingerprint="b" * 64,
    )
    with pytest.raises(ValueError, match="commitment fingerprint mismatch"):
        authorize_single_sealed_holdout_release(
            manifest,
            expected_manifest_fingerprint=manifest_fingerprint(manifest),
            holdout_commitment=substituted,
        )


def test_holdout_release_rejects_missing_survivor_and_bad_hash() -> None:
    items = tuple(replace(item, dsr_probability=0.2) for item in tournament())
    manifest = manifest_for(items)
    with pytest.raises(ValueError, match="no gate-surviving"):
        authorize_single_sealed_holdout_release(
            manifest,
            expected_manifest_fingerprint=manifest_fingerprint(manifest),
            holdout_commitment=commitment(),
        )

    with pytest.raises(ValueError, match="lowercase sha256"):
        commit_sealed_holdout_before_fitting(
            PROTOCOL,
            sealed_holdout_id="v70-holdout-001",
            sealed_holdout_fingerprint="NOT-A-HASH",
        )
