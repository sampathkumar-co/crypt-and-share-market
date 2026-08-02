from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CADENCE = ROOT / ".github" / "workflows" / "forward-evidence-cadence.yml"
COLLECTOR = ROOT / ".github" / "workflows" / "forward-market-state-v20.yml"


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


def test_v20_collector_has_no_independent_hourly_schedule() -> None:
    text = COLLECTOR.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "pull_request:" in text
    assert "schedule:" not in text
    assert 'cron: "17 * * * *"' not in text
