from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

from tradebot.research.intraday_alpha_lab_v70 import (
    CandidateEvidence,
    CandidateFamily,
    PromotionDecision,
    evaluate_candidate,
    evidence_fingerprint,
)
from tradebot.research.intraday_tournament_v70 import rank_survivors


SELECTION_SCHEMA_VERSION = "7.0-pre-holdout-selection-v1"
HOLDOUT_RELEASE_SCHEMA_VERSION = "7.0-sealed-holdout-release-v1"
REQUIRED_FAMILIES = frozenset(CandidateFamily)


@dataclass(frozen=True)
class TrialLedgerEntry:
    candidate_id: str
    family: CandidateFamily
    permanent_trial_number: int
    evidence_fingerprint: str


@dataclass(frozen=True)
class PreHoldoutSelectionManifest:
    schema_version: str
    protocol_fingerprint: str
    selected_candidate_id: str | None
    ranked_candidate_ids: tuple[str, ...]
    rejected: Mapping[str, tuple[str, ...]]
    trial_ledger: tuple[TrialLedgerEntry, ...]
    holdout_opened: bool = False
    paper_only: bool = True
    authorizes_trading: bool = False


@dataclass(frozen=True)
class SealedHoldoutRelease:
    schema_version: str
    manifest_fingerprint: str
    selected_candidate_id: str
    sealed_holdout_id: str
    sealed_holdout_fingerprint: str
    consumed_holdout_ids: tuple[str, ...]
    evaluation_count: int = 1
    paper_only: bool = True
    authorizes_trading: bool = False


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def protocol_fingerprint(protocol_text: str) -> str:
    normalized = protocol_text.replace("\r\n", "\n").strip() + "\n"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def manifest_fingerprint(manifest: PreHoldoutSelectionManifest) -> str:
    return hashlib.sha256(_canonical(asdict(manifest)).encode("utf-8")).hexdigest()


def holdout_release_fingerprint(release: SealedHoldoutRelease) -> str:
    return hashlib.sha256(_canonical(asdict(release)).encode("utf-8")).hexdigest()


def _validate_trial_ledger(
    evidence: Sequence[CandidateEvidence], ledger: Sequence[TrialLedgerEntry]
) -> None:
    if not ledger:
        raise ValueError("permanent trial ledger is required")
    numbers = [entry.permanent_trial_number for entry in ledger]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError("trial numbers must be contiguous, permanent, and start at one")
    ids = [entry.candidate_id for entry in ledger]
    if len(ids) != len(set(ids)):
        raise ValueError("trial ledger candidate identifiers must be unique")
    by_id = {item.candidate_id: item for item in evidence}
    if set(ids) != set(by_id):
        raise ValueError("trial ledger must cover every evaluated candidate exactly once")
    for entry in ledger:
        candidate = by_id[entry.candidate_id]
        if entry.family is not candidate.family:
            raise ValueError("trial ledger family mismatch")
        if entry.evidence_fingerprint != evidence_fingerprint(candidate):
            raise ValueError("trial ledger evidence fingerprint mismatch")
        if candidate.trial_count != len(ledger):
            raise ValueError("candidate trial_count must equal the permanent ledger size")


def build_pre_holdout_manifest(
    protocol_text: str,
    evidence: Sequence[CandidateEvidence],
    trial_ledger: Sequence[TrialLedgerEntry],
    *,
    holdout_opened: bool = False,
) -> PreHoldoutSelectionManifest:
    if holdout_opened:
        raise ValueError("pre-holdout selection must be frozen before opening the holdout")
    if not protocol_text.strip():
        raise ValueError("frozen protocol text is required")
    if not evidence:
        raise ValueError("candidate evidence is required")
    ids = [item.candidate_id for item in evidence]
    if len(ids) != len(set(ids)):
        raise ValueError("candidate identifiers must be unique")
    families = {item.family for item in evidence}
    if families != REQUIRED_FAMILIES:
        raise ValueError("the first tournament must include exactly the four frozen families")

    _validate_trial_ledger(evidence, trial_ledger)
    decisions: dict[str, PromotionDecision] = {
        item.candidate_id: evaluate_candidate(item) for item in evidence
    }
    survivors = [item for item in evidence if decisions[item.candidate_id].passed]
    ranked = rank_survivors(survivors)
    rejected = {
        candidate_id: decision.reasons
        for candidate_id, decision in sorted(decisions.items())
        if not decision.passed
    }
    return PreHoldoutSelectionManifest(
        schema_version=SELECTION_SCHEMA_VERSION,
        protocol_fingerprint=protocol_fingerprint(protocol_text),
        selected_candidate_id=ranked[0].candidate_id if ranked else None,
        ranked_candidate_ids=tuple(item.candidate_id for item in ranked),
        rejected=rejected,
        trial_ledger=tuple(trial_ledger),
    )


def verify_manifest_is_non_authorizing(manifest: PreHoldoutSelectionManifest) -> None:
    if manifest.holdout_opened:
        raise ValueError("pre-holdout manifest cannot claim an opened holdout")
    if not manifest.paper_only or manifest.authorizes_trading:
        raise ValueError("selection manifest must remain paper-only and non-authorizing")


def authorize_single_sealed_holdout_release(
    manifest: PreHoldoutSelectionManifest,
    *,
    expected_manifest_fingerprint: str,
    sealed_holdout_id: str,
    sealed_holdout_fingerprint: str,
    consumed_holdout_ids: Sequence[str] = (),
) -> SealedHoldoutRelease:
    verify_manifest_is_non_authorizing(manifest)
    actual_manifest_fingerprint = manifest_fingerprint(manifest)
    if expected_manifest_fingerprint != actual_manifest_fingerprint:
        raise ValueError("frozen selection manifest fingerprint mismatch")
    if manifest.selected_candidate_id is None:
        raise ValueError("no gate-surviving candidate is eligible for holdout release")
    if not sealed_holdout_id.strip():
        raise ValueError("sealed holdout identifier is required")
    if len(sealed_holdout_fingerprint) != 64 or any(
        character not in "0123456789abcdef" for character in sealed_holdout_fingerprint
    ):
        raise ValueError("sealed holdout fingerprint must be lowercase sha256")
    consumed = tuple(consumed_holdout_ids)
    if len(consumed) != len(set(consumed)):
        raise ValueError("consumed holdout registry contains duplicates")
    if sealed_holdout_id in consumed:
        raise ValueError("consumed holdout may never be reused")
    return SealedHoldoutRelease(
        schema_version=HOLDOUT_RELEASE_SCHEMA_VERSION,
        manifest_fingerprint=actual_manifest_fingerprint,
        selected_candidate_id=manifest.selected_candidate_id,
        sealed_holdout_id=sealed_holdout_id,
        sealed_holdout_fingerprint=sealed_holdout_fingerprint,
        consumed_holdout_ids=tuple(sorted((*consumed, sealed_holdout_id))),
    )


def verify_holdout_release_is_non_authorizing(release: SealedHoldoutRelease) -> None:
    if release.evaluation_count != 1:
        raise ValueError("sealed holdout release must permit exactly one evaluation")
    if release.sealed_holdout_id not in release.consumed_holdout_ids:
        raise ValueError("released holdout must be recorded as permanently consumed")
    if not release.paper_only or release.authorizes_trading:
        raise ValueError("holdout release must remain paper-only and non-authorizing")
