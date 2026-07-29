from __future__ import annotations

import json

import pytest

from tradebot.research.status import load_research_status, require_continuous_paper_authorization


def test_missing_ledger_fails_closed(tmp_path):
    status = load_research_status(tmp_path / "missing.json")
    assert status["source_valid"] is False
    assert status["approved_strategies"] == []
    assert status["continuous_paper_authorized"] is False
    assert status["live_trading_authorized"] is False


def test_malicious_live_authorization_fails_closed(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({
        "schema_version": "1.0",
        "paper_only": True,
        "approved_strategies": ["momentum"],
        "continuous_paper_authorized": True,
        "live_trading_authorized": True,
        "experiments": [],
    }), encoding="utf-8")
    status = load_research_status(path)
    assert status["source_valid"] is False
    assert status["approved_strategies"] == []


def test_valid_forward_paper_ledger_authorizes_only_named_strategy(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({
        "schema_version": "1.0",
        "deployment_mode": "forward_paper_candidate",
        "paper_only": True,
        "approved_strategies": ["momentum"],
        "continuous_paper_authorized": True,
        "live_trading_authorized": False,
        "experiments": [],
    }), encoding="utf-8")
    status = require_continuous_paper_authorization("momentum", path)
    assert status["source_valid"] is True
    assert status["source_fingerprint"]
    with pytest.raises(ValueError, match="not approved"):
        require_continuous_paper_authorization("breakout", path)
