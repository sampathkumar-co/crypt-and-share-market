from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "v25-historical-proxy-screen.yml"


def test_track_b_results_are_persisted_only_to_isolated_branch() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "historical-results/v25" in text
    assert "results/latest.json" in text
    assert 'results/run-${GITHUB_RUN_ID}.json' in text
    assert "git -C /tmp/v25-historical-results push origin HEAD:historical-results/v25" in text
    assert "forward-data/v2" not in text


def test_track_b_write_permission_is_job_scoped() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "permissions:\n  contents: read" in text
    assert "run-fixed-historical-screen:" in text
    assert "    permissions:\n      contents: write" in text
    assert "if: github.event_name != 'pull_request'" in text
