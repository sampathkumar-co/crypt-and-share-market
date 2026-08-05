from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import common_accounting_tournament_v60 as v60


def _report(*, standard: float, stress: float, cash: float, drawdown: float, concentration: float, actions: int, dependency: str | None = None):
    report = {
        "paper_only": True,
        "authorizes_trading": False,
        "standard": {
            "net_compounded_return": standard,
            "cash_benchmark_compounded_return": cash,
            "window_returns": {f"y{i}": 0.04 + i * 0.001 for i in range(5)},
            "crypto_action_days": actions,
            "maximum_drawdown": drawdown,
            "maximum_positive_year_share": concentration,
        },
        "stress": {
            "net_compounded_return": stress,
            "window_returns": {f"y{i}": 0.03 + i * 0.001 for i in range(5)},
        },
        "gates": {"a": True, "b": True},
    }
    if dependency is not None:
        report["v312_dependency_report_sha256"] = dependency
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def _pair(monkeypatch):
    left = _report(
        standard=0.31,
        stress=0.30,
        cash=0.18,
        drawdown=0.02,
        concentration=0.45,
        actions=44,
    )
    monkeypatch.setattr(v60, "EXPECTED_V312_SHA256", left["report_sha256"])
    right = _report(
        standard=0.30,
        stress=0.29,
        cash=0.19,
        drawdown=0.025,
        concentration=0.48,
        actions=41,
        dependency=left["report_sha256"],
    )
    monkeypatch.setattr(v60, "EXPECTED_V32_SHA256", right["report_sha256"])
    return left, right


def test_conservative_champion_uses_worse_exchange_and_higher_cash(monkeypatch):
    left, right = _pair(monkeypatch)
    evidence = v60.build_conservative_champion(left, right)
    assert evidence.standard_return == 0.30
    assert evidence.stress_return == 0.29
    assert evidence.cash_return == 0.19
    assert evidence.actions == 41
    assert evidence.maximum_drawdown == 0.025
    assert evidence.maximum_positive_window_share == 0.48


def test_missing_delay_and_trade_concentration_fail_closed(monkeypatch):
    left, right = _pair(monkeypatch)
    evidence = v60.build_conservative_champion(left, right)
    gates = v60.evaluate_material_gates(evidence)
    assert not gates.positive_delayed_execution
    assert not gates.trade_concentration_at_most_20pct
    assert not gates.passed


def test_report_never_claims_breakthrough_with_pending_evidence(monkeypatch):
    left, right = _pair(monkeypatch)
    report = v60.build_report(left, right)
    assert report["status"] == "MATERIAL_GATES_FAILED"
    assert report["historical_breakthrough"] is False
    assert report["forward_breakthrough"] is False
    assert report["authorizes_trading"] is False


def test_mutated_report_hash_is_rejected(monkeypatch):
    left, right = _pair(monkeypatch)
    broken = deepcopy(left)
    broken["standard"]["net_compounded_return"] = 99.0
    with pytest.raises(v60.TournamentV60Error, match="hash does not match"):
        v60.build_conservative_champion(broken, right)


def test_unlinked_coinbase_replication_is_rejected(monkeypatch):
    left, right = _pair(monkeypatch)
    right["v312_dependency_report_sha256"] = "0" * 64
    right["report_sha256"] = hashlib.sha256(
        canonical_json({k: value for k, value in right.items() if k != "report_sha256"}).encode("utf-8")
    ).hexdigest()
    monkeypatch.setattr(v60, "EXPECTED_V32_SHA256", right["report_sha256"])
    with pytest.raises(v60.TournamentV60Error, match="not cryptographically linked"):
        v60.build_conservative_champion(left, right)
