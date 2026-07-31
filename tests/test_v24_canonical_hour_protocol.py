from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADDENDUM = ROOT / "research" / "V24_CANONICAL_HOUR_SELECTION_ADDENDUM.md"


def test_v24_canonical_hour_rule_is_frozen_before_evaluator() -> None:
    text = ADDENDUM.read_text(encoding="utf-8")

    assert "frozen before v2.4 evaluator implementation" in text
    assert "before any" in text
    assert "return, P&L, drawdown" in text
    assert "Group valid snapshots by exact `hour_bucket_utc`" in text
    assert "sort by `snapshot_id` ascending and select the first item" in text
    assert "earliest valid" in text


def test_v24_duplicate_and_holdout_handling_is_fail_closed() -> None:
    text = ADDENDUM.read_text(encoding="utf-8")

    assert "non-canonical" in text
    assert "never counted as another hour" in text
    assert "manifest/hash mismatch fail closed" in text
    assert "decision referencing a non-canonical duplicate" in text
    assert "A missing canonical hour breaks continuity" in text
    assert "may not read or disclose" in text
    assert "authorizes_trading=false" in text
