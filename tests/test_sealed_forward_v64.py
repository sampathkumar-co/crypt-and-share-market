from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json

import pytest

from tradebot.research.sealed_forward_v64 import (
    CANDIDATE_REPORT_SHA256,
    SealedForwardPrediction,
    SealedForwardV64Error,
    append_prediction,
    dual_source_target,
    protocol_sha256,
    verify_prediction,
)


DIGEST = "a" * 64


def make_prediction(**overrides):
    executable = datetime(2026, 8, 6, tzinfo=timezone.utc)
    values = {
        "decision_date": date(2026, 8, 6).isoformat(),
        "created_at": (executable - timedelta(minutes=10)).isoformat(),
        "earliest_executable_at": executable.isoformat(),
        "horizon_end_at": (executable + timedelta(days=1)).isoformat(),
        "candidate_report_sha256": CANDIDATE_REPORT_SHA256,
        "protocol_sha256": protocol_sha256(),
        "implementation_sha256": DIGEST,
        "binance_data_sha256": DIGEST,
        "coinbase_data_sha256": DIGEST,
        "binance_target": {"BTC": 0.05, "ETH": 0.04},
        "coinbase_target": {"BTC": 0.03, "ETH": 0.05},
        "final_target": {"BTC": 0.03, "ETH": 0.04},
        "cash_weight": 0.93,
        "genuine_decision": True,
        "reason": "frozen_dual_source_consensus",
    }
    values.update(overrides)
    return SealedForwardPrediction(**values)


def test_dual_source_target_is_symmetric_minimum():
    assert dual_source_target(
        {"BTC": 0.05, "ETH": 0.04}, {"BTC": 0.03, "ETH": 0.05}
    ) == {"BTC": 0.03, "ETH": 0.04}


def test_prediction_is_deterministic_and_verifiable():
    prediction = make_prediction()
    assert len(prediction.record_sha256) == 64
    assert verify_prediction(prediction.payload(), prediction.record_sha256)


def test_pre_programme_backfill_is_rejected():
    with pytest.raises(SealedForwardV64Error, match="pre-programme"):
        make_prediction(decision_date="2026-08-05")


def test_prediction_must_precede_execution():
    moment = datetime(2026, 8, 6, tzinfo=timezone.utc).isoformat()
    with pytest.raises(SealedForwardV64Error, match="must precede"):
        make_prediction(created_at=moment, earliest_executable_at=moment)


def test_target_cannot_exceed_source_minimum():
    with pytest.raises(SealedForwardV64Error, match="dual-source minimum"):
        make_prediction(final_target={"BTC": 0.05, "ETH": 0.04}, cash_weight=0.91)


def test_live_or_continuous_authorization_is_rejected():
    with pytest.raises(SealedForwardV64Error, match="authorization boundary"):
        make_prediction(authorizes_trading=True)
    with pytest.raises(SealedForwardV64Error, match="authorization boundary"):
        make_prediction(authorizes_continuous_paper=True)


def test_append_is_duplicate_safe_and_hash_chained(tmp_path: Path):
    first = make_prediction()
    first_path = append_prediction(tmp_path, first)
    envelope = json.loads(first_path.read_text())
    assert envelope["record_sha256"] == first.record_sha256
    with pytest.raises(SealedForwardV64Error, match="duplicate"):
        append_prediction(tmp_path, first)

    second = make_prediction(
        decision_date="2026-08-07",
        created_at="2026-08-06T23:50:00+00:00",
        earliest_executable_at="2026-08-07T00:00:00+00:00",
        horizon_end_at="2026-08-08T00:00:00+00:00",
        previous_record_sha256=first.record_sha256,
    )
    append_prediction(tmp_path, second)


def test_wrong_chain_is_rejected(tmp_path: Path):
    first = make_prediction()
    append_prediction(tmp_path, first)
    second = make_prediction(
        decision_date="2026-08-07",
        created_at="2026-08-06T23:50:00+00:00",
        earliest_executable_at="2026-08-07T00:00:00+00:00",
        horizon_end_at="2026-08-08T00:00:00+00:00",
        previous_record_sha256="b" * 64,
    )
    with pytest.raises(SealedForwardV64Error, match="chain"):
        append_prediction(tmp_path, second)
