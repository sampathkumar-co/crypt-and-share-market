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


def test_watchdog_rebinds_when_a_decision_run_is_cancelled_by_concurrency() -> None:
    text = WATCHDOG.read_text(encoding="utf-8")

    assert 'if [[ "$run_conclusion" == "cancelled" ]]; then' in text
    assert 'replacement_run_id=$(gh run list --workflow "$workflow"' in text
    assert 'run_id="$replacement_run_id"' in text
    assert "rebinding to active run" in text
    assert "no replacement is active yet, continuing bounded persistence wait" in text


def test_watchdog_retries_transient_forward_data_fetches_but_still_fails_closed() -> None:
    text = WATCHDOG.read_text(encoding="utf-8")

    assert "fetch_forward_data() {" in text
    assert "for attempt in 1 2 3; do" in text
    assert "if git fetch --quiet origin forward-data/v2; then" in text
    assert "Transient forward-data/v2 fetch failure" in text
    assert "Unable to refresh forward-data/v2 after bounded retries; failing closed." in text
    assert text.count("fetch_forward_data") >= 4
