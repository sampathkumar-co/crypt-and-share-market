from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CADENCE = ROOT / ".github" / "workflows" / "forward-evidence-cadence.yml"


def test_redundant_cadence_dispatches_existing_watchdog_only() -> None:
    text = CADENCE.read_text(encoding="utf-8")

    assert 'cron: "5,25,45 * * * *"' in text
    assert "forward-evidence-watchdog.yml" in text
    assert "gh workflow run" in text
    assert "--ref main" in text
    assert "actions: write" in text
    assert "forward-market-state-v20.yml" not in text
    assert "tradebot" not in text


def test_redundant_cadence_avoids_duplicate_active_watchdogs() -> None:
    text = CADENCE.read_text(encoding="utf-8")

    assert 'status == "queued" or .status == "in_progress"' in text
    assert "now - 1800" in text
    assert "A recent watchdog is already active" in text
    assert "no evidence or strategy bytes were modified" in text
