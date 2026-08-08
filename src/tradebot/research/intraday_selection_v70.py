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


SELECTION_SCHEMA_VERSION = "7.0-pre-holdout-selection-v2"
HOLDOUT_COMMITMENT_SCHEMA_VERSION = "7.0-sealed-holdout-commitment-v1"
HOLDOUT_RELEASE_SCHEMA_VERSION = "7.0-sealed-holdout-release-v2"
REQUIRED_FAMILIES = frozenset(CandidateFamily)


@dataclass(frozen=True)
class TrialLedgerEntry:
    candidate_id: str
    family: CandidateFamily
    permanent_trial_number: int
    evidence_fingerprint: str


@dataclass(frozen=True)
class SealedHoldoutCommitment:
    schema_version: str
    protocol_fingerprint: str
    sealed_holdout_id: str
    sealed_holdout_fingerprint: str
    committed_before_fitting: bool = True
    opened: bool = False
    paper_only: bool = True
    authorizes_trading: bool = False


@dataclass(frozen=True)
class PreHoldoutSelectionManifest:
    schema_version: str
    protocol_fingerprint: str
    selected_candidate_id: str | None
    ranked_candidate_ids: tuple[str, ...]
    rejected: Mapping[str, tuple[str, ...]]
    trial_ledger: tuple[TrialLedgerEntry, ...]
    holdout_commitment_fingerprint: str | None = None
    sealed_holdout_id: str | None = None
    sealed_holdout_fingerprint: str | None = None
    holdout_opened: bool = False
    paper_only: bool = True
    authorizes_trading: bool = False


@dataclass(frozen=True)
class SealedHoldoutRelease:
    schema_version: str
    manifest_fingerprint: str
    holdout_commitment_fingerprint: str
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


def holdout_commitment_fingerprint(commitment: SealedHoldoutCommitment) -> str:
    return hashlib.sha256(_canonical(asdict(commitment)).encode("utf-8")).hexdigest()


def manifest_fingerprint(manifest: PreHoldoutSelectionManifest) -> str:
    return hashlib.sha256(_canonical(asdict(manifest)).encode("utf-8")).hexdigest()


def holdout_release_fingerprint(release: SealedHoldoutRelease) -> str:
    return hashlib.sha256(_canonical(asdict(release)).encode("utf-8")).hexdigest()


def _validate_sha256(value: str, *, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be lowercase sha256")


def commit_sealed_holdout_before_fitting(
    protocol_text: str,
    *,
    sealed_holdout_id: str,
    sealed_holdout_fingerprint: str,
) -> SealedHoldoutCommitment:
    if not protocol_text.strip():
        raise ValueError("frozen protocol text is required")
    if not sealed_holdout_id.strip():
        raise ValueError("sealed holdout identifier is required")
    _validate_sha256(sealed_holdout_fingerprint, field="sealed holdout fingerprint")
    return SealedHoldoutCommitment(
        schema_version=HOLDOUT_COMMITMENT_SCHEMA_VERSION,
        protocol_fingerprint=protocol_fingerprint(protocol_text),
        sealed_holdout_id=sealed_holdout_id,
        sealed_holdout_fingerprint=sealed_holdout_fingerprint,
    )


def verify_holdout_commitment_is_pre_fit(commitment: SealedHoldoutCommitment) -> None:
    if commitment.schema_version != HOLDOUT_COMMITMENT_SCHEMA_VERSION:
        raise ValueError("holdout commitment schema version mismatch")
    _validate_sha256(commitment.protocol_fingerprint, field="protocol fingerprint")
    if not commitment.committed_before_fitting or commitment.opened:
        raise ValueError("holdout commitment must be frozen and unopened before fitting")
    if not commitment.paper_only or commitment.authorizes_trading:
        raise ValueError("holdout commitment must remain paper-only and non-authorizing")
    if not commitment.sealed_holdout_id.strip():
        raise ValueError("sealed holdout identifier is required")
    _validate_sha256(commitment.sealed_holdout_fingerprint, field="sealed holdout fingerprint")


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
    holdout_commitment: SealedHoldoutCommitment,
    holdout_opened: bool = False,
) -> PreHoldoutSelectionManifest:
    if holdout_opened:
        raise ValueError("pre-holdout selection must be frozen before opening the holdout")
    if not protocol_text.strip():
        raise ValueError("frozen protocol text is required")
    verify_holdout_commitment_is_pre_fit(holdout_commitment)
    frozen_protocol_fingerprint = protocol_fingerprint(protocol_text)
    if holdout_commitment.protocol_fingerprint != frozen_protocol_fingerprint:
        raise ValueError("holdout commitment does not match the frozen protocol")
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
        protocol_fingerprint=frozen_protocol_fingerprint,
        selected_candidate_id=ranked[0].candidate_id if ranked else None,
        ranked_candidate_ids=tuple(item.candidate_id for item in ranked),
        rejected=rejected,
        trial_ledger=tuple(trial_ledger),
        holdout_commitment_fingerprint=holdout_commitment_fingerprint(holdout_commitment),
        sealed_holdout_id=holdout_commitment.sealed_holdout_id,
        sealed_holdout_fingerprint=holdout_commitment.sealed_holdout_fingerprint,
    )


def verify_manifest_is_non_authorizing(manifest: PreHoldoutSelectionManifest) -> None:
    if manifest.schema_version != SELECTION_SCHEMA_VERSION:
        raise ValueError("selection manifest schema version mismatch")
    _validate_sha256(manifest.protocol_fingerprint, field="protocol fingerprint")
    if manifest.holdout_opened:
        raise ValueError("pre-holdout manifest cannot claim an opened holdout")
    if not manifest.paper_only or manifest.authorizes_trading:
        raise ValueError("selection manifest must remain paper-only and non-authorizing")
    if not manifest.holdout_commitment_fingerprint:
        raise ValueError("selection manifest requires a pre-fit holdout commitment")
    _validate_sha256(
        manifest.holdout_commitment_fingerprint,
        field="holdout commitment fingerprint",
    )
    if not manifest.sealed_holdout_id or not manifest.sealed_holdout_fingerprint:
        raise ValueError("selection manifest requires a frozen sealed holdout identity")
    _validate_sha256(manifest.sealed_holdout_fingerprint, field="sealed holdout fingerprint")


def authorize_single_sealed_holdout_release(
    manifest: PreHoldoutSelectionManifest,
    *,
    expected_manifest_fingerprint: str,
    holdout_commitment: SealedHoldoutCommitment,
    consumed_holdout_ids: Sequence[str] = (),
) -> SealedHoldoutRelease:
    verify_manifest_is_non_authorizing(manifest)
    verify_holdout_commitment_is_pre_fit(holdout_commitment)
    actual_manifest_fingerprint = manifest_fingerprint(manifest)
    if expected_manifest_fingerprint != actual_manifest_fingerprint:
        raise ValueError("frozen selection manifest fingerprint mismatch")
    actual_commitment_fingerprint = holdout_commitment_fingerprint(holdout_commitment)
    if manifest.holdout_commitment_fingerprint != actual_commitment_fingerprint:
        raise ValueError("sealed holdout commitment fingerprint mismatch")
    if holdout_commitment.protocol_fingerprint != manifest.protocol_fingerprint:
        raise ValueError("sealed holdout commitment protocol mismatch")
    if manifest.sealed_holdout_id != holdout_commitment.sealed_holdout_id:
        raise ValueError("sealed holdout identity differs from pre-fit commitment")
    if manifest.sealed_holdout_fingerprint != holdout_commitment.sealed_holdout_fingerprint:
        raise ValueError("sealed holdout fingerprint differs from pre-fit commitment")
    if manifest.selected_candidate_id is None:
        raise ValueError("no gate-surviving candidate is eligible for holdout release")
    consumed = tuple(consumed_holdout_ids)
    if len(consumed) != len(set(consumed)):
        raise ValueError("consumed holdout registry contains duplicates")
    if holdout_commitment.sealed_holdout_id in consumed:
        raise ValueError("consumed holdout may never be reused")
    return SealedHoldoutRelease(
        schema_version=HOLDOUT_RELEASE_SCHEMA_VERSION,
        manifest_fingerprint=actual_manifest_fingerprint,
        holdout_commitment_fingerprint=actual_commitment_fingerprint,
        selected_candidate_id=manifest.selected_candidate_id,
        sealed_holdout_id=holdout_commitment.sealed_holdout_id,
        sealed_holdout_fingerprint=holdout_commitment.sealed_holdout_fingerprint,
        consumed_holdout_ids=tuple(sorted((*consumed, holdout_commitment.sealed_holdout_id))),
    )


def verify_holdout_release_is_non_authorizing(release: SealedHoldoutRelease) -> None:
    if release.schema_version != HOLDOUT_RELEASE_SCHEMA_VERSION:
        raise ValueError("holdout release schema version mismatch")
    _validate_sha256(release.manifest_fingerprint, field="manifest fingerprint")
    _validate_sha256(
        release.holdout_commitment_fingerprint,
        field="holdout commitment fingerprint",
    )
    _validate_sha256(release.sealed_holdout_fingerprint, field="sealed holdout fingerprint")
    if not release.selected_candidate_id.strip() or not release.sealed_holdout_id.strip():
        raise ValueError("holdout release requires frozen candidate and holdout identifiers")
    if release.evaluation_count != 1:
        raise ValueError("sealed holdout release must permit exactly one evaluation")
    consumed = tuple(release.consumed_holdout_ids)
    if not consumed or any(not holdout_id.strip() for holdout_id in consumed):
        raise ValueError("consumed holdout registry requires non-empty identifiers")
    if len(consumed) != len(set(consumed)):
        raise ValueError("consumed holdout registry contains duplicates")
    if consumed.count(release.sealed_holdout_id) != 1:
        raise ValueError("released holdout must be recorded exactly once as permanently consumed")
    if not release.holdout_commitment_fingerprint:
        raise ValueError("released holdout must remain bound to its pre-fit commitment")
    if not release.paper_only or release.authorizes_trading:
        raise ValueError("holdout release must remain paper-only and non-authorizing")
