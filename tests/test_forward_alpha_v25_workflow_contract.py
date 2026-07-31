from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DECISIONS = ROOT / ".github" / "workflows" / "forward-alpha-decisions-v25.yml"
VERIFY = ROOT / ".github" / "workflows" / "forward-alpha-v25.yml"
WATCHDOG = ROOT / ".github" / "workflows" / "forward-alpha-v25-watchdog.yml"
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

    assert 'cron: "49,59 * * * *"' in text
    assert "data/forward-alpha-v25/decisions/$safe.json" in text
    assert "forward-alpha-decisions-v25.yml" in text
    assert "A recent v2.5 decision run is already active" in text
    assert "gh workflow run forward-alpha-decisions-v25.yml --ref main" in text
    assert "No market snapshot, decision byte, strategy threshold or evaluation gate was modified" in text


def test_readiness_workflow_keeps_performance_and_holdout_locked() -> None:
    path = ROOT / ".github" / "workflows" / "forward-alpha-readiness-v25.yml"
    text = path.read_text(encoding="utf-8")

    assert "tradebot-forward-alpha-v25-readiness" in text
    assert 'report["required_eligible_hours"] == 1448' in text
    assert 'report["purge_hours_locked"] == 8' in text
    assert 'report["required_future_snapshot_hours"] == 9' in text
    assert 'report["performance_calculated"] is False' in text
    assert 'report["holdout_unlocked"] is False' in text
    assert 'report["authorizes_shadow_paper"] is False' in text
