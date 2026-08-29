from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "forward-snapshot-decision-handoff.yml"


def test_handoff_is_bound_to_successful_persisted_v20_runs() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert 'workflows: ["Forward Market State v2.0"]' in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "github.event.workflow_run.event != 'pull_request'" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert 'forward-market-state-v20-normalized-$SOURCE_RUN_ID' in text
    assert "The workflow artifact was not persisted to forward-data/v2" in text


def test_handoff_accepts_only_a_proven_duplicate_noop_without_an_artifact() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "Classify the source run before requiring an artifact" in text
    assert 'select(.name == "collect-and-verify" or .name == "persist-normalized-history")' in text
    assert '"${conclusions[0]}" == "collect-and-verify=skipped"' in text
    assert '"${conclusions[1]}" == "persist-normalized-history=skipped"' in text
    assert "Source run was an intentional duplicate/no-op; no snapshot artifact is expected." in text
    assert "Unexpected missing normalized artifact for source run" in text
    assert "if: steps.source.outputs.has_snapshot == 'true'" in text


def test_handoff_fails_closed_and_verifies_both_decisions() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "source hour $target_hour is no longer the newest canonical snapshot" in text
    assert "gh workflow run market-state-router-v21.yml --ref main" in text
    assert "gh workflow run forward-alpha-decisions-v23.yml --ref main" in text
    assert "data/market-state-router/decisions/$SAFE_HOUR.json" in text
    assert "data/forward-alpha-v23/decisions/$SAFE_HOUR.json" in text
    assert 'assert report["data_cutoff_utc"] == target' in text
    assert 'assert report["paper_only"] is True' in text
    assert 'assert report["authorizes_trading"] is False' in text
