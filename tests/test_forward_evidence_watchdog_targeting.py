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
    assert 'if [[ "$snapshot_present" != "true" && "$active_v20" == "0" ]]; then' in text
    assert '[[ "$phase" != "decisions"' not in text
