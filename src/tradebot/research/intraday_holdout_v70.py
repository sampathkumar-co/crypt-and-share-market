from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

from tradebot.research.intraday_alpha_lab_v70 import MAX_DRAWDOWN
from tradebot.research.intraday_selection_v70 import (
    PreHoldoutSelectionManifest,
    SealedHoldoutRelease,
    holdout_release_fingerprint,
    manifest_fingerprint,
    verify_holdout_release_is_non_authorizing,
)


HOLDOUT_RESULT_SCHEMA_VERSION = "7.0-sealed-holdout-result-v2"
REQUIRED_SOURCES = frozenset(("binance", "coinbase"))


@dataclass(frozen=True)
class HoldoutAction:
    action_id: str
    sequence_index: int
    source: str
    standard_excess_return: float
    stress_excess_return: float
    delayed_stress_excess_return: float
    target_changed: bool


@dataclass(frozen=True)
class SealedHoldoutResult:
    schema_version: str
    release_fingerprint: str
    manifest_fingerprint: str
    selected_candidate_id: str
    sealed_holdout_id: str
    sealed_holdout_fingerprint: str
    source_action_counts: Mapping[str, int]
    target_changing_actions: int
    standard_compounded_excess: float
    stress_compounded_excess: float
    delayed_stress_compounded_excess: float
    maximum_drawdown: float
    passed: bool
    reasons: tuple[str, ...]
    paper_only: bool = True
    authorizes_trading: bool = False


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def holdout_result_fingerprint(result: SealedHoldoutResult) -> str:
    return hashlib.sha256(_canonical(asdict(result)).encode("utf-8")).hexdigest()


def _validate_sha256(value: str, *, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be lowercase sha256")


def _compound(values: Sequence[float]) -> float:
    wealth = 1.0
    for value in values:
        number = float(value)
        if not math.isfinite(number) or number <= -1.0:
            raise ValueError("holdout returns must be finite and greater than -100%")
        wealth *= 1.0 + number
    return wealth - 1.0


def _maximum_drawdown(values: Sequence[float]) -> float:
    wealth = 1.0
    peak = 1.0
    maximum = 0.0
    for value in values:
        number = float(value)
        if not math.isfinite(number) or number <= -1.0:
            raise ValueError("holdout returns must be finite and greater than -100%")
        wealth *= 1.0 + number
        peak = max(peak, wealth)
        maximum = max(maximum, (peak - wealth) / peak)
    return maximum


def _logical_action_id(item: HoldoutAction) -> str:
    prefix, separator, logical_id = item.action_id.partition(":")
    if separator != ":" or prefix != item.source or not logical_id.strip() or ":" in logical_id:
        raise ValueError("holdout action identifiers must use canonical source:logical-id form")
    return logical_id


def evaluate_single_sealed_holdout(
    manifest: PreHoldoutSelectionManifest,
    release: SealedHoldoutRelease,
    actions: Sequence[HoldoutAction],
    *,
    expected_release_fingerprint: str,
    observed_holdout_fingerprint: str,
) -> SealedHoldoutResult:
    verify_holdout_release_is_non_authorizing(release)
    actual_manifest_fingerprint = manifest_fingerprint(manifest)
    if release.manifest_fingerprint != actual_manifest_fingerprint:
        raise ValueError("release does not match the frozen pre-holdout manifest")
    actual_release_fingerprint = holdout_release_fingerprint(release)
    if expected_release_fingerprint != actual_release_fingerprint:
        raise ValueError("sealed holdout release fingerprint mismatch")
    _validate_sha256(observed_holdout_fingerprint, field="observed holdout fingerprint")
    if observed_holdout_fingerprint != release.sealed_holdout_fingerprint:
        raise ValueError("opened holdout does not match the pre-fit sealed fingerprint")
    if manifest.selected_candidate_id is None:
        raise ValueError("manifest has no selected candidate")
    if release.selected_candidate_id != manifest.selected_candidate_id:
        raise ValueError("released candidate differs from frozen selection")
    if not actions:
        raise ValueError("sealed holdout observations are required")

    action_ids = [item.action_id for item in actions]
    if any(not value.strip() for value in action_ids):
        raise ValueError("holdout action identifiers must be non-empty and unique")
    if any(type(item.sequence_index) is not int or item.sequence_index < 0 for item in actions):
        raise ValueError("holdout sequence indices must be non-negative integers")
    if any(type(item.target_changed) is not bool for item in actions):
        raise ValueError("holdout target_changed values must be booleans")
    sources = {item.source for item in actions}
    if sources != REQUIRED_SOURCES:
        raise ValueError("holdout requires exactly Binance and Coinbase observations")

    logical_ids_by_action = {item.action_id: _logical_action_id(item) for item in actions}
    if len(action_ids) != len(set(action_ids)):
        raise ValueError("holdout action identifiers must be non-empty and unique")
    by_source = {source: [item for item in actions if item.source == source] for source in REQUIRED_SOURCES}
    counts = {source: len(items) for source, items in by_source.items()}
    if len(set(counts.values())) != 1:
        raise ValueError("independent sources must contain equal action counts")

    logical_sets: dict[str, set[str]] = {}
    sequence_maps: dict[str, dict[str, int]] = {}
    for source, items in by_source.items():
        logical_ids = [logical_ids_by_action[item.action_id] for item in items]
        if len(logical_ids) != len(set(logical_ids)):
            raise ValueError(f"duplicate logical action in {source} holdout evidence")
        sequence_indices = [item.sequence_index for item in items]
        if len(sequence_indices) != len(set(sequence_indices)):
            raise ValueError(f"duplicate sequence index in {source} holdout evidence")
        logical_sets[source] = set(logical_ids)
        sequence_maps[source] = {
            logical_ids_by_action[item.action_id]: item.sequence_index for item in items
        }
    if len({frozenset(values) for values in logical_sets.values()}) != 1:
        raise ValueError("independent sources must cover identical logical actions")
    if sequence_maps["binance"] != sequence_maps["coinbase"]:
        raise ValueError("independent sources must agree on action chronology")

    ordered_by_source = {
        source: sorted(items, key=lambda item: item.sequence_index)
        for source, items in by_source.items()
    }
    source_standard = {
        source: _compound([item.standard_excess_return for item in items])
        for source, items in ordered_by_source.items()
    }
    source_stress = {
        source: _compound([item.stress_excess_return for item in items])
        for source, items in ordered_by_source.items()
    }
    source_delayed = {
        source: _compound([item.delayed_stress_excess_return for item in items])
        for source, items in ordered_by_source.items()
    }
    source_drawdown = {
        source: _maximum_drawdown([item.stress_excess_return for item in items])
        for source, items in ordered_by_source.items()
    }

    # Binance and Coinbase are independent replications of one logical paper path,
    # not two simultaneously traded portfolios. Report the conservative source
    # result rather than compounding both sources together.
    standard_compounded = min(source_standard.values())
    stress_compounded = min(source_stress.values())
    delayed_compounded = min(source_delayed.values())
    maximum_drawdown = max(source_drawdown.values())

    logical_target_changes: dict[str, bool] = {}
    for item in actions:
        logical_id = logical_ids_by_action[item.action_id]
        previous = logical_target_changes.setdefault(logical_id, item.target_changed)
        if previous is not item.target_changed:
            raise ValueError("independent sources disagree on target-changing actions")
    target_changing_actions = sum(logical_target_changes.values())

    reasons: list[str] = []
    if standard_compounded <= 0.0:
        reasons.append("holdout_standard_excess_not_positive")
    if stress_compounded <= 0.0:
        reasons.append("holdout_stress_excess_not_positive")
    if delayed_compounded <= 0.0:
        reasons.append("holdout_delayed_stress_not_positive")
    if any(value <= 0.0 for value in source_stress.values()):
        reasons.append("holdout_independent_source_stress_failed")
    if any(value <= 0.0 for value in source_delayed.values()):
        reasons.append("holdout_independent_source_delay_failed")
    if maximum_drawdown > MAX_DRAWDOWN:
        reasons.append("holdout_drawdown_gate_failed")
    if target_changing_actions == 0:
        reasons.append("holdout_has_no_genuine_actions")

    return SealedHoldoutResult(
        schema_version=HOLDOUT_RESULT_SCHEMA_VERSION,
        release_fingerprint=actual_release_fingerprint,
        manifest_fingerprint=actual_manifest_fingerprint,
        selected_candidate_id=release.selected_candidate_id,
        sealed_holdout_id=release.sealed_holdout_id,
        sealed_holdout_fingerprint=release.sealed_holdout_fingerprint,
        source_action_counts=counts,
        target_changing_actions=target_changing_actions,
        standard_compounded_excess=standard_compounded,
        stress_compounded_excess=stress_compounded,
        delayed_stress_compounded_excess=delayed_compounded,
        maximum_drawdown=maximum_drawdown,
        passed=not reasons,
        reasons=tuple(reasons),
    )


def verify_holdout_result_is_non_authorizing(result: SealedHoldoutResult) -> None:
    if result.schema_version != HOLDOUT_RESULT_SCHEMA_VERSION:
        raise ValueError("holdout result schema version mismatch")
    _validate_sha256(result.release_fingerprint, field="release fingerprint")
    _validate_sha256(result.manifest_fingerprint, field="manifest fingerprint")
    _validate_sha256(result.sealed_holdout_fingerprint, field="sealed holdout fingerprint")
    if not result.selected_candidate_id.strip() or not result.sealed_holdout_id.strip():
        raise ValueError("holdout result requires frozen candidate and holdout identifiers")
    if set(result.source_action_counts) != REQUIRED_SOURCES:
        raise ValueError("holdout result requires exactly Binance and Coinbase source counts")
    counts = tuple(result.source_action_counts[source] for source in sorted(REQUIRED_SOURCES))
    if any(type(count) is not int or count <= 0 for count in counts) or len(set(counts)) != 1:
        raise ValueError("holdout result source counts must be positive and aligned")
    if type(result.target_changing_actions) is not int or result.target_changing_actions < 0:
        raise ValueError("holdout result target-changing action count must be non-negative")
    if result.target_changing_actions > counts[0]:
        raise ValueError("holdout result target-changing actions exceed logical action count")
    numeric_values = (
        result.standard_compounded_excess,
        result.stress_compounded_excess,
        result.delayed_stress_compounded_excess,
        result.maximum_drawdown,
    )
    if not all(math.isfinite(float(value)) for value in numeric_values):
        raise ValueError("holdout result metrics must be finite")
    if result.maximum_drawdown < 0.0 or result.maximum_drawdown > 1.0:
        raise ValueError("holdout result drawdown must be between zero and one")
    if result.passed != (len(result.reasons) == 0):
        raise ValueError("holdout result pass state contradicts recorded reasons")
    if result.passed:
        if result.standard_compounded_excess <= 0.0:
            raise ValueError("passing holdout result requires positive standard excess")
        if result.stress_compounded_excess <= 0.0:
            raise ValueError("passing holdout result requires positive stress excess")
        if result.delayed_stress_compounded_excess <= 0.0:
            raise ValueError("passing holdout result requires positive delayed stress excess")
        if result.maximum_drawdown > MAX_DRAWDOWN:
            raise ValueError("passing holdout result exceeds frozen drawdown gate")
        if result.target_changing_actions == 0:
            raise ValueError("passing holdout result requires genuine target-changing actions")
    if not result.paper_only or result.authorizes_trading:
        raise ValueError("sealed holdout result must remain paper-only and non-authorizing")
