from dataclasses import asdict

import pytest

from tradebot.v5.evidence import (
    DataManifest,
    ExperimentResult,
    ExperimentSpec,
    append_record,
)
from tradebot.v5.validation import assert_no_overlap, purged_walk_forward_splits


SHA_A = "a" * 64
SHA_B = "b" * 64


def manifest() -> DataManifest:
    return DataManifest(
        source="coinbase-public",
        retrieval_time_utc="2026-08-05T00:05:00Z",
        available_at_utc="2026-08-05T00:00:00Z",
        raw_sha256=SHA_A,
        normalized_sha256=SHA_B,
        rows=1000,
    )


def test_spec_digest_is_deterministic_and_paper_only() -> None:
    spec = ExperimentSpec(
        experiment_id="v50-test-001",
        parent_ids=(),
        hypothesis="trend persists after costs",
        code_commit="abcdef123456",
        config={"stress_cost_bps": 40, "assets": ["BTC-USD", "ETH-USD"]},
        data_manifests=(manifest(),),
        test_intervals=("2025-Q1",),
    )
    assert spec.digest() == spec.digest()
    assert len(spec.digest()) == 64


def test_manifest_fails_closed_on_missing_rows() -> None:
    bad = DataManifest(**{**asdict(manifest()), "missing_rows": 1})
    with pytest.raises(ValueError, match="fails closed"):
        bad.validate()


def test_future_availability_is_rejected() -> None:
    bad = DataManifest(
        **{
            **asdict(manifest()),
            "available_at_utc": "2026-08-05T00:06:00Z",
        }
    )
    with pytest.raises(ValueError, match="before it is available"):
        bad.validate()


def test_result_never_authorizes_trading() -> None:
    result = ExperimentResult(
        spec_digest=SHA_A,
        status="REJECTED",
        metrics={"net_return": -0.01},
        authorizes_trading=True,
    )
    with pytest.raises(ValueError, match="cannot authorize trading"):
        result.validate()


def test_append_record_preserves_prior_lines() -> None:
    prior = ['{"old":true}']
    record = {"paper_only": True, "authorizes_trading": False, "id": "new"}
    updated = append_record(prior, record)
    assert updated[0] == prior[0]
    assert len(updated) == 2


def test_purged_walk_forward_has_chronology_and_embargo() -> None:
    folds = list(
        purged_walk_forward_splits(
            260,
            train_size=100,
            validation_size=30,
            test_size=20,
            purge=5,
            embargo=3,
        )
    )
    assert len(folds) >= 2
    first = folds[0]
    assert first.validation_start - first.train_end == 5
    assert first.test_start - first.validation_end == 5
    assert_no_overlap(folds, embargo=3)


def test_invalid_split_parameters_fail_closed() -> None:
    with pytest.raises(ValueError):
        list(
            purged_walk_forward_splits(
                100,
                train_size=50,
                validation_size=20,
                test_size=10,
                purge=-1,
                embargo=0,
            )
        )
