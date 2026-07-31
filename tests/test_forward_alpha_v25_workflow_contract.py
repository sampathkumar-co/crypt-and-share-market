from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DECISIONS = ROOT / ".github" / "workflows" / "forward-alpha-decisions-v25.yml"
VERIFY = ROOT / ".github" / "workflows" / "forward-alpha-v25.yml"
WATCHDOG = ROOT / ".github" / "workflows" / "forward-evidence-watchdog.yml"
PROTOCOL = ROOT / "research" / "V25_HIGH_CONVICTION_ALPHA_PROTOCOL.md"


def test_v25_protocol_and_verification_are_isolated() -> None:
    protocol = PROTOCOL.read_text(encoding="utf-8")
    workflow = VERIFY.read_text(encoding="utf-8")

    assert "residual momentum with microstructure confirmation" in protocol
    assert "funding/basis state transition" in protocol
    assert "sweep-and-replenishment continuation" in protocol
    assert "Maximum 15% weight per asset" in protocol
    assert "At least 70% intended cash" in protocol
    assert "forward_alpha_v25.py" in workflow
    assert "test_forward_alpha_v25.py" in workflow
    assert "market_state_router.py" in workflow
    assert "forward_alpha_candidates.py" in workflow


def test_v25_decision_workflow_is_append_only_and_bounded() -> None:
    text = DECISIONS.read_text(encoding="utf-8")

    assert 'cron: "54 * * * *"' in text
    assert "tradebot-forward-alpha-v25" in text
    assert "data/forward-alpha-v25" in text
    assert "v25-compact/decisions" in text
    assert "v25-compact/manifests" in text
    assert "group: forward-data-v2-writer" in text
    assert "Refusing to replace a different same-hour v2.5 decision" in text
    assert 'report["minimum_cash_weight"] >= 0.70' in text
    assert '<= 0.30 + 1e-12' in text
    assert '<= 0.15 + 1e-12' in text
    assert 'report["authorizes_shadow_paper"] is False' in text
    assert '"authorizes_shadow_paper": False' in text


def test_watchdog_recovers_only_missing_v25_decisions() -> None:
    text = WATCHDOG.read_text(encoding="utf-8")

    assert "data/forward-alpha-v25/decisions/$safe.json" in text
    assert "active_v25=" in text
    assert "forward-alpha-decisions-v25.yml" in text
    assert 'if [[ ! -f "$v25" && "$active_v25" == "0" ]]' in text
    assert "gh workflow run forward-alpha-decisions-v25.yml --ref main" in text
