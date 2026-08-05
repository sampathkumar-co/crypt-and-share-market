from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import dual_source_consensus_v632 as v632


def _report(name: str) -> dict:
    status = v632.EXPECTED_STATUS[name]
    source_prefix = "1" if name == "v6.1" else "2"
    report = {
        "schema_version": name,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_continuous_paper": False,
        "retrospective_dates_exposed": True,
        "untouched_historical_dates": False,
        "status": status,
        "member_count": 16,
        "members": [{"model_id": "m"}],
        "conservative": {"standard_return": 0.3},
        "material_gates": {"profit": True},
        "statistical_gates": {"bootstrap": False},
        "sources": {
            "binance": {
                "standard_relative_series_sha256": source_prefix * 64
            },
            "coinbase": {
                "standard_relative_series_sha256": ("3" if name == "v6.1" else "4") * 64
            },
        },
        "deflated_sharpe": {"passed": True},
        "rank_stability": {
            "passed": False,
            "median_percentile_rank": 0.5,
            "percentile_ranks": [0.5, 0.6],
        },
        "volatile_dependency_sha": "a" * 64,
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def test_semantic_fingerprint_ignores_volatile_lineage_and_rank_list():
    report = _report("v6.1")
    left = v632.semantic_fingerprint(report)
    changed = deepcopy(report)
    changed["volatile_dependency_sha"] = "b" * 64
    changed["rank_stability"]["percentile_ranks"] = [0.1, 0.9]
    changed.pop("report_sha256")
    changed["report_sha256"] = hashlib.sha256(
        canonical_json(changed).encode("utf-8")
    ).hexdigest()
    assert v632.semantic_fingerprint(changed) == left


def test_semantic_fingerprint_changes_when_daily_series_changes():
    report = _report("v6.2")
    left = v632.semantic_fingerprint(report)
    report["sources"]["coinbase"]["standard_relative_series_sha256"] = "f" * 64
    assert v632.semantic_fingerprint(report) != left


def test_dependency_self_hash_still_fails_closed(monkeypatch):
    report = _report("v6.1")
    monkeypatch.setitem(
        v632.EXPECTED_SEMANTIC,
        "v6.1",
        v632.semantic_fingerprint(report),
    )
    report["conservative"]["standard_return"] = 99.0
    with pytest.raises(
        v632.base.DualSourceConsensusV63Error,
        match="self-hash",
    ):
        v632.validate_dependency(
            report,
            expected_sha="ignored",
            expected_status=v632.EXPECTED_STATUS["v6.1"],
            name="v6.1",
        )


def test_dependency_returns_semantic_not_whole_report_hash(monkeypatch):
    report = _report("v6.2")
    semantic = v632.semantic_fingerprint(report)
    monkeypatch.setitem(v632.EXPECTED_SEMANTIC, "v6.2", semantic)
    result = v632.validate_dependency(
        report,
        expected_sha="ignored",
        expected_status=v632.EXPECTED_STATUS["v6.2"],
        name="v6.2",
    )
    assert result == semantic
    assert result != report["report_sha256"]
