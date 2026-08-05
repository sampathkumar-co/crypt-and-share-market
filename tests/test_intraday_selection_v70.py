from __future__ import annotations

from dataclasses import replace

import pytest

from tradebot.research.intraday_alpha_lab_v70 import CandidateEvidence, CandidateFamily, evidence_fingerprint
from tradebot.research.intraday_selection_v70 import (
    TrialLedgerEntry,
    build_pre_holdout_manifest,
    manifest_fingerprint,
    verify_manifest_is_non_authorizing,
)


PROTOCOL = "frozen v7 protocol\n"


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


def test_selects_best_gate_survivor_before_holdout() -> None:
    items = tournament()
    manifest = build_pre_holdout_manifest(PROTOCOL, items, ledger(items))
    assert manifest.selected_candidate_id == "breakout"
    assert manifest.ranked_candidate_ids == ("breakout", "relative", "trend", "reversal")
    assert manifest.holdout_opened is False
    verify_manifest_is_non_authorizing(manifest)


def test_failed_candidate_is_recorded_not_silently_removed() -> None:
    items = list(tournament())
    items[2] = replace(items[2], dsr_probability=0.2)
    frozen = tuple(items)
    manifest = build_pre_holdout_manifest(PROTOCOL, frozen, ledger(frozen))
    assert "breakout" not in manifest.ranked_candidate_ids
    assert "deflated_sharpe_failed" in manifest.rejected["breakout"]


def test_rejects_holdout_contact_and_trial_reset() -> None:
    items = tournament()
    with pytest.raises(ValueError, match="before opening"):
        build_pre_holdout_manifest(PROTOCOL, items, ledger(items), holdout_opened=True)
    reset = tuple(replace(item, trial_count=1) for item in items)
    with pytest.raises(ValueError, match="ledger size"):
        build_pre_holdout_manifest(PROTOCOL, reset, ledger(reset))


def test_rejects_missing_frozen_family_and_fingerprint_tampering() -> None:
    items = tournament()
    with pytest.raises(ValueError, match="exactly the four"):
        build_pre_holdout_manifest(PROTOCOL, items[:-1], ledger(items[:-1]))
    bad = list(ledger(items))
    bad[0] = replace(bad[0], evidence_fingerprint="0" * 64)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        build_pre_holdout_manifest(PROTOCOL, items, tuple(bad))


def test_manifest_fingerprint_is_deterministic() -> None:
    items = tournament()
    manifest = build_pre_holdout_manifest(PROTOCOL, items, ledger(items))
    assert manifest_fingerprint(manifest) == manifest_fingerprint(manifest)
