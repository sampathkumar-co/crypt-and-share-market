from pathlib import Path


def test_valid_240h_workflow_has_independent_lane() -> None:
    workflow = Path(".github/workflows/v27-fivefold-valid-240h.yml").read_text(
        encoding="utf-8"
    )
    assert "group: v27-fivefold-valid-240h-${{ github.ref }}" in workflow
    assert "effective_state_assembly_warmup_hours\"] == 240" in workflow
    assert "historical-results/v27" in workflow
    assert "valid-240h-run-${GITHUB_RUN_ID}.json" in workflow
    assert "latest-valid-240h.json" in workflow
    assert "result-branch/results/latest.json" not in workflow
    assert "forward-data/v2" not in workflow
    assert "authorizes_trading\"] is False" in workflow
    assert "authorizes_shadow_paper\"] is False" in workflow


def test_valid_lane_does_not_modify_frozen_sources() -> None:
    workflow = Path(".github/workflows/v27-fivefold-valid-240h.yml").read_text(
        encoding="utf-8"
    )
    assert "tradebot-v27-fivefold-discovery" in workflow
    assert "git hash-object research/V27_FIVEFOLD_MECHANISM_DISCOVERY_PROTOCOL.md" in workflow
    assert "git hash-object src/tradebot/research/historical_discovery_v27.py" in workflow


def test_valid_persistence_retries_non_fast_forward_races() -> None:
    workflow = Path(".github/workflows/v27-fivefold-valid-240h.yml").read_text(
        encoding="utf-8"
    )
    assert "for attempt in 1 2 3" in workflow
    assert "git rebase origin/historical-results/v27" in workflow
    assert "Unable to persist protocol-valid v2.7 result" in workflow
