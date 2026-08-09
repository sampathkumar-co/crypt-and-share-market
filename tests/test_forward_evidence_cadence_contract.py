from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CADENCE = ROOT / ".github" / "workflows" / "forward-evidence-cadence.yml"
COLLECTOR = ROOT / ".github" / "workflows" / "forward-market-state-v20.yml"
SENTINEL = ROOT / ".github" / "workflows" / "forward-evidence-continuity-sentinel.yml"


def test_redundant_cadence_dispatches_existing_watchdog_only() -> None:
    text = CADENCE.read_text(encoding="utf-8")

    assert 'cron: "5,25,45 * * * *"' in text
    assert "forward-evidence-watchdog.yml" in text
    assert "gh workflow run" in text
    assert "--ref main" in text
    assert "actions: write" in text
    assert "forward-market-state-v20.yml" not in text
    assert "tradebot" not in text


def test_redundant_cadence_suppression_is_target_hour_aware() -> None:
    text = CADENCE.read_text(encoding="utf-8")

    assert 'status == "queued" or .status == "in_progress"' in text
    assert "export TARGET_HOUR_START=$(date -u +'%Y-%m-%dT%H:00:00Z')" in text
    assert ".createdAt >= env.TARGET_HOUR_START" in text
    assert "--jq --arg" not in text
    assert "now - 1800" not in text
    assert "created for the current target hour is already active" in text
    assert "no evidence or strategy bytes were modified" in text


def test_v20_collector_has_five_independent_hourly_attempts() -> None:
    text = COLLECTOR.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "pull_request:" in text
    assert "schedule:" in text
    for minute in (7, 17, 27, 37, 47):
        assert f'cron: "{minute} * * * *"' in text


def test_cross_hour_sentinel_survives_one_missed_scheduler_hour() -> None:
    text = SENTINEL.read_text(encoding="utf-8")

    assert 'cron: "3 * * * *"' in text
    assert "timeout-minutes: 115" in text
    assert "next_hour_epoch=$(( (now_epoch / 3600 + 1) * 3600 ))" in text
    assert "first_probe_epoch=$(( next_hour_epoch + 8 * 60 ))" in text
    assert "sleep 1200" in text
    assert "sleep 900" in text
    assert text.count("dispatch_watchdog_if_needed") >= 5


def test_cross_hour_sentinel_is_orchestration_only_and_fail_closed() -> None:
    text = SENTINEL.read_text(encoding="utf-8")

    assert "actions: write" in text
    assert "contents: read" in text
    assert "forward-evidence-watchdog.yml" in text
    assert "gh workflow run" in text
    assert "--ref main" in text
    assert 'status == \\"queued\\" or .status == \\"in_progress\\"' in text
    assert "createdAt >=" in text
    assert "does not create, rewrite, or backfill evidence" in text
    assert "without modifying strategy, gate, holdout, cost, exposure, or evidence bytes" in text
    assert "tradebot" not in text
    assert "forward-market-state-v20.yml" not in text
    assert "curl " not in text
