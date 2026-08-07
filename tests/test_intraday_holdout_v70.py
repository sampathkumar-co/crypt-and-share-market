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
    commit_sealed_holdout_before_fitting,
    holdout_commitment_fingerprint,
    holdout_release_fingerprint,
    manifest_fingerprint,
)


PROTOCOL = "frozen v7 protocol\n"


def _commitment():
    return commit_sealed_holdout_before_fitting(
        PROTOCOL,
        sealed_holdout_id="sealed-2025q4",
        sealed_holdout_fingerprint="b" * 64,
    )


def _manifest() -> PreHoldoutSelectionManifest:
    frozen = _commitment()
    return PreHoldoutSelectionManifest(
        schema_version="7.0-pre-holdout-selection-v2",
        protocol_fingerprint=frozen.protocol_fingerprint,
        selected_candidate_id="trend-001",
        ranked_candidate_ids=("trend-001",),
        rejected={},
        trial_ledger=(),
        holdout_commitment_fingerprint=holdout_commitment_fingerprint(frozen),
        sealed_holdout_id=frozen.sealed_holdout_id,
        sealed_holdout_fingerprint=frozen.sealed_holdout_fingerprint,
    )


def _release(manifest: PreHoldoutSelectionManifest):
    return authorize_single_sealed_holdout_release(
        manifest,
        expected_manifest_fingerprint=manifest_fingerprint(manifest),
        holdout_commitment=_commitment(),
    )


def _actions(value: float = 0.002) -> tuple[HoldoutAction, ...]:
    rows = []
    for source in ("binance", "coinbase"):
        for index in range(3):
            rows.append(
                HoldoutAction(
                    action_id=f"{source}:action-{index}",
                    sequence_index=index,
                    source=source,
                    standard_excess_return=value + 0.001,
                    stress_excess_return=value,
                    delayed_stress_excess_return=value / 2,
                    target_changed=index > 0,
                )
            )
    return tuple(rows)


def _evaluate(actions: tuple[HoldoutAction, ...]):
    manifest = _manifest()
    release = _release(manifest)
    return evaluate_single_sealed_holdout(
        manifest,
        release,
        actions,
        expected_release_fingerprint=holdout_release_fingerprint(release),
    )


def test_evaluates_one_frozen_holdout_without_authorizing_trading() -> None:
    result = _evaluate(_actions())
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


def test_rejects_noncanonical_or_source_mismatched_action_identity() -> None:
    actions = tuple(
        replace(item, action_id="coinbase:action-0")
        if item.action_id == "binance:action-0"
        else item
        for item in _actions()
    )
    with pytest.raises(ValueError, match="canonical source:logical-id"):
        _evaluate(actions)


def test_rejects_non_boolean_target_change_flags() -> None:
    actions = tuple(
        replace(item, target_changed=1)
        if item.action_id == "binance:action-1"
        else item
        for item in _actions()
    )
    with pytest.raises(ValueError, match="must be booleans"):
        _evaluate(actions)


def test_negative_replication_fails_closed() -> None:
    actions = tuple(
        replace(item, stress_excess_return=-0.01, delayed_stress_excess_return=-0.01)
        if item.source == "coinbase"
        else item
        for item in _actions()
    )
    result = _evaluate(actions)
    assert not result.passed
    assert "holdout_independent_source_stress_failed" in result.reasons
    assert "holdout_independent_source_delay_failed" in result.reasons


def test_source_disagreement_on_target_change_is_rejected() -> None:
    actions = tuple(
        replace(item, target_changed=False)
        if item.action_id == "coinbase:action-1"
        else item
        for item in _actions()
    )
    with pytest.raises(ValueError, match="disagree on target-changing"):
        _evaluate(actions)


def test_source_chronology_disagreement_is_rejected() -> None:
    actions = tuple(
        replace(item, sequence_index=2)
        if item.action_id == "coinbase:action-1"
        else replace(item, sequence_index=1)
        if item.action_id == "coinbase:action-2"
        else item
        for item in _actions()
    )
    with pytest.raises(ValueError, match="agree on action chronology"):
        _evaluate(actions)


def test_independent_sources_are_not_double_compounded() -> None:
    result = _evaluate(_actions(value=0.01))
    expected_one_source = (1.01**3) - 1.0
    double_counted = (1.01**6) - 1.0
    assert result.stress_compounded_excess == pytest.approx(expected_one_source)
    assert result.stress_compounded_excess != pytest.approx(double_counted)


def test_reported_result_uses_conservative_source_path() -> None:
    actions = tuple(
        replace(
            item,
            standard_excess_return=0.002,
            stress_excess_return=0.001,
            delayed_stress_excess_return=0.0005,
        )
        if item.source == "coinbase"
        else item
        for item in _actions(value=0.01)
    )
    result = _evaluate(actions)
    assert result.standard_compounded_excess == pytest.approx((1.002**3) - 1.0)
    assert result.stress_compounded_excess == pytest.approx((1.001**3) - 1.0)
    assert result.delayed_stress_compounded_excess == pytest.approx((1.0005**3) - 1.0)


def test_drawdown_uses_explicit_chronology_and_worst_source() -> None:
    actions = tuple(
        replace(item, stress_excess_return=-0.04)
        if item.source == "coinbase" and item.sequence_index == 1
        else replace(item, stress_excess_return=0.05)
        if item.source == "coinbase"
        else item
        for item in _actions()
    )
    result = _evaluate(actions)
    assert result.maximum_drawdown == pytest.approx(0.04)
    assert result.passed
    assert "holdout_drawdown_gate_failed" not in result.reasons


def test_non_authorizing_invariant_fails_closed() -> None:
    result = _evaluate(_actions())
    with pytest.raises(ValueError, match="paper-only"):
        verify_holdout_result_is_non_authorizing(replace(result, authorizes_trading=True))
