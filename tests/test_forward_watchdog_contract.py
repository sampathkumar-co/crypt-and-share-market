from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WATCHDOG = ROOT / ".github" / "workflows" / "forward-evidence-watchdog.yml"


def test_watchdog_waits_for_an_already_active_current_hour_collector() -> None:
    text = WATCHDOG.read_text(encoding="utf-8")

    assert "active_v20=$(gh run list --workflow forward-market-state-v20.yml" in text
    assert 'elif [[ "$snapshot_present" != "true" ]]; then' in text
    assert "A current-hour v2.0 collector is already active" in text
    assert "wait_for_v20=true" in text
    assert 'if [[ "$snapshot_present" != "true" && "$wait_for_v20" == "true" ]]; then' in text
    assert "for attempt in $(seq 1 40)" in text
    assert "failing closed without dispatching decisions" in text
