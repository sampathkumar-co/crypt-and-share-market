from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "market-state-router-v21.yml"


def test_identical_same_hour_decision_retains_first_inventory() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "Refusing to replace a different same-hour v2.1 decision" in text
    assert "Existing v2.1 decision has no preserved inventory" in text
    assert "Identical same-hour v2.1 decision already preserved" in text
    assert "retaining its first hash-verified inventory" in text
    assert "Refusing orphaned same-hour v2.1 inventory" in text


def test_preserved_inventory_hashes_and_decision_inputs_are_verified() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert 'assert inventory["forward_data_branch"] == "forward-data/v2"' in text
    assert 'assert inventory["snapshot_files"] == len(snapshots)' in text
    assert "assert len(by_name) == len(snapshots)" in text
    assert "assert path.is_file(), path" in text
    assert 'expected = item["snapshot_sha256"]' in text
    assert "hashlib.sha256(path.read_bytes()).hexdigest() == expected" in text
    assert 'required = {f"{item[\'snapshot_id\']}.json" for item in decision["input_snapshots"]}' in text
    assert "assert required.issubset(by_name)" in text
    assert 'assert decision["paper_only"] is True' in text
    assert 'assert decision["authorizes_trading"] is False' in text
