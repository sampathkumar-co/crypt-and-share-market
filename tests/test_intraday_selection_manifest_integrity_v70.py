from __future__ import annotations

from dataclasses import replace

import pytest

from tradebot.research.intraday_alpha_lab_v70 import (
    CandidateEvidence,
    CandidateFamily,
    evidence_fingerprint,
)
from tradebot.research.intraday_selection_v70 import (
    TrialLedgerEntry,
    build_pre_holdout_manifest,
    commit_sealed_holdout_before_fitting,
    verify_manifest_is_non_authorizing,
)


PROTOCOL = "frozen v7 protocol\n"


def _candidate(candidate_id: str, family: CandidateFamily, stress: float) -> CandidateEvidence:
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
        trial_count=4,
    )


def _manifest():
    items = (
        _candidate("trend", CandidateFamily.HOURLY_TREND, 0.04),
        _candidate("reversal", CandidateFamily.SHOCK_REVERSAL, 0.03),
        _candidate("breakout", CandidateFamily.VOLATILITY_BREAKOUT, 0.06),
        _candidate("relative", CandidateFamily.RELATIVE_STRENGTH, 0.05),
    )
    ledger = tuple(
        TrialLedgerEntry(item.candidate_id, item.family, index, evidence_fingerprint(item))
        for index, item in enumerate(items, start=1)
    )
    commitment = commit_sealed_holdout_before_fitting(
        PROTOCOL,
        sealed_holdout_id="v70-holdout-001",
        sealed_holdout_fingerprint="a" * 64,
    )
    return build_pre_holdout_manifest(
        PROTOCOL,
        items,
        ledger,
        holdout_commitment=commitment,
    )


def test_manifest_verifier_rejects_trial_ledger_tampering() -> None:
    manifest = _manifest()
    duplicated = (manifest.trial_ledger[0], manifest.trial_ledger[0], *manifest.trial_ledger[2:])
    with pytest.raises(ValueError, match="trial numbers|unique"):
        verify_manifest_is_non_authorizing(replace(manifest, trial_ledger=duplicated))

    malformed = list(manifest.trial_ledger)
    malformed[0] = replace(malformed[0], evidence_fingerprint="bad")
    with pytest.raises(ValueError, match="trial evidence fingerprint must be lowercase sha256"):
        verify_manifest_is_non_authorizing(replace(manifest, trial_ledger=tuple(malformed)))


def test_manifest_verifier_rejects_selection_partition_tampering() -> None:
    manifest = _manifest()
    with pytest.raises(ValueError, match="first ranked survivor"):
        verify_manifest_is_non_authorizing(replace(manifest, selected_candidate_id="trend"))

    with pytest.raises(ValueError, match="both rank and reject"):
        verify_manifest_is_non_authorizing(
            replace(manifest, rejected={"trend": ("fabricated_rejection",)})
        )

    with pytest.raises(ValueError, match="account for every permanent trial"):
        verify_manifest_is_non_authorizing(
            replace(manifest, ranked_candidate_ids=manifest.ranked_candidate_ids[:-1])
        )


def test_manifest_verifier_rejects_unrecorded_or_empty_rejections() -> None:
    manifest = _manifest()
    with pytest.raises(ValueError, match="exist in the permanent trial ledger"):
        verify_manifest_is_non_authorizing(
            replace(manifest, rejected={"invented": ("failed",)})
        )

    rejected_candidate = replace(manifest, ranked_candidate_ids=manifest.ranked_candidate_ids[:-1])
    rejected_candidate = replace(
        rejected_candidate,
        rejected={manifest.ranked_candidate_ids[-1]: ()},
        selected_candidate_id=manifest.ranked_candidate_ids[0],
    )
    with pytest.raises(ValueError, match="require recorded reasons"):
        verify_manifest_is_non_authorizing(rejected_candidate)
