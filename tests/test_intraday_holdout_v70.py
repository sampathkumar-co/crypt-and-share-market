from dataclasses import replace

import pytest

from tradebot.research.intraday_holdout_v70 import (
    HoldoutAction,
    evaluate_single_sealed_holdout,
    holdout_result_fingerprint,
    verify_holdout_result_is_non_authorizing,
)
from tradebot.research.intraday_selection_v70 import (
    PreHoldoutSelectionManifest,
    authorize_single_sealed_holdout_release,
    holdout_release_fingerprint,
    manifest_fingerprint,
)


def _manifest() -> PreHoldoutSelectionManifest:
    return PreHoldoutSelectionManifest(
        schema_version="7.0-pre-holdout-selection-v1",
        protocol_fingerprint="a" * 64,
        selected_candidate_id="trend-001",
        ranked_candidate_ids=("trend-001",),
        rejected={},
        trial_ledger=(),
    )


def _release(manifest: PreHoldoutSelectionManifest):
    return authorize_single_sealed_holdout_release(
        manifest,
        expected_manifest_fingerprint=manifest_fingerprint(manifest),
        sealed_holdout_id="sealed-2025q4",
        sealed_holdout_fingerprint="b" * 64,
    )


def _actions(value: float = 0.002) -> tuple[HoldoutAction, ...]:
    rows = []
    for source in ("binance", "coinbase"):
        for index in range(3):
            rows.append(
                HoldoutAction(
                    action_id=f"{source}:action-{index}",
                    source=source,
                    standard_excess_return=value + 0.001,
                    stress_excess_return=value,
                    delayed_stress_excess_return=value / 2,
                    target_changed=index > 0,
                )
            )
    return tuple(rows)


def test_evaluates_one_frozen_holdout_without_authorizing_trading() -> None:
    manifest = _manifest()
    release = _release(manifest)
    result = evaluate_single_sealed_holdout(
        manifest,
        release,
        _actions(),
        expected_release_fingerprint=holdout_release_fingerprint(release),
    )
    assert result.passed
    assert result.target_changing_actions == 2
    assert set(result.source_action_counts) == {"binance", "coinbase"}
    assert len(holdout_result_fingerprint(result)) == 64
    verify_holdout_result_is_non_authorizing(result)


def test_rejects_manifest_or_release_tampering() -> None:
    manifest = _manifest()
    release = _release(manifest)
    with pytest.raises(ValueError, match="release fingerprint mismatch"):
        evaluate_single_sealed_holdout(
            manifest,
            release,
            _actions(),
            expected_release_fingerprint="0" * 64,
        )
    with pytest.raises(ValueError, match="does not match"):
        evaluate_single_sealed_holdout(
            replace(manifest, protocol_fingerprint="c" * 64),
            release,
            _actions(),
            expected_release_fingerprint=holdout_release_fingerprint(release),
        )


def test_requires_exact_independent_source_alignment() -> None:
    manifest = _manifest()
    release = _release(manifest)
    actions = _actions()[:-1]
    with pytest.raises(ValueError, match="equal action counts"):
        evaluate_single_sealed_holdout(
            manifest,
            release,
            actions,
            expected_release_fingerprint=holdout_release_fingerprint(release),
        )


def test_negative_replication_fails_closed() -> None:
    manifest = _manifest()
    release = _release(manifest)
    actions = tuple(
        replace(item, stress_excess_return=-0.01, delayed_stress_excess_return=-0.01)
        if item.source == "coinbase"
        else item
        for item in _actions()
    )
    result = evaluate_single_sealed_holdout(
        manifest,
        release,
        actions,
        expected_release_fingerprint=holdout_release_fingerprint(release),
    )
    assert not result.passed
    assert "holdout_independent_source_stress_failed" in result.reasons
    assert "holdout_independent_source_delay_failed" in result.reasons


def test_source_disagreement_on_target_change_is_rejected() -> None:
    manifest = _manifest()
    release = _release(manifest)
    actions = tuple(
        replace(item, target_changed=False)
        if item.action_id == "coinbase:action-1"
        else item
        for item in _actions()
    )
    with pytest.raises(ValueError, match="disagree on target-changing"):
        evaluate_single_sealed_holdout(
            manifest,
            release,
            actions,
            expected_release_fingerprint=holdout_release_fingerprint(release),
        )


def test_non_authorizing_invariant_fails_closed() -> None:
    manifest = _manifest()
    release = _release(manifest)
    result = evaluate_single_sealed_holdout(
        manifest,
        release,
        _actions(),
        expected_release_fingerprint=holdout_release_fingerprint(release),
    )
    with pytest.raises(ValueError, match="paper-only"):
        verify_holdout_result_is_non_authorizing(replace(result, authorizes_trading=True))
