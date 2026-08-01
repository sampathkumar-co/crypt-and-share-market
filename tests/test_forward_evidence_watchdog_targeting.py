from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WATCHDOG = ROOT / ".github" / "workflows" / "forward-evidence-watchdog.yml"


def test_v20_active_run_suppression_is_current_hour_target_aware() -> None:
    text = WATCHDOG.read_text(encoding="utf-8")
    assert "current_hour_started_at=$(date -u +%Y-%m-%dT%H:00:00Z)" in text
    assert '.createdAt >= \\"$current_hour_started_at\\"' in text
    assert "now - 3600" not in text


def test_every_watchdog_phase_can_recover_a_missing_current_hour_snapshot() -> None:
    text = WATCHDOG.read_text(encoding="utf-8")
    assert '[[ "$phase" != "decisions"' not in text
    assert '"$current_minute" -lt "$recovery_deadline_minute"' in text


def test_rollover_guard_keeps_mid_hour_recovery_and_fails_closed_late() -> None:
    text = WATCHDOG.read_text(encoding="utf-8")
    assert "recovery_deadline_minute=45" in text
    assert "inside the rollover safety window; failing closed without dispatch" in text
    assert "current_minute=$((10#$(date -u +%M)))" in text


def test_watchdog_has_an_early_recovery_opportunity_before_rollover_cutoff() -> None:
    text = WATCHDOG.read_text(encoding="utf-8")
    assert '- cron: "17 * * * *"' in text
    assert '- cron: "37 * * * *"' in text
    assert '- cron: "57 * * * *"' in text
