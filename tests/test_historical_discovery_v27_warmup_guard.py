from __future__ import annotations

from tradebot.research import historical_discovery_v26 as v26
from tradebot.research import historical_discovery_v27 as v27
from tradebot.research import historical_discovery_v27_runner as runner


def test_guard_sets_exact_protocol_warmup(monkeypatch) -> None:
    captured: dict[str, int] = {}

    def fake_run_discovery(*, max_workers: int):
        captured["workers"] = max_workers
        captured["warmup"] = v26.WARMUP_HOURS
        return {
            "fingerprints": {"implementation_sha256": "a", "protocol_sha256": "b"},
            "report_sha256": "stale",
        }

    monkeypatch.setattr(v27, "run_discovery", fake_run_discovery)
    original = v26.WARMUP_HOURS
    try:
        report = runner.run_guarded_discovery(max_workers=7)
    finally:
        v26.WARMUP_HOURS = original

    assert captured == {"workers": 7, "warmup": 240}
    assert report["effective_state_assembly_warmup_hours"] == 240
    assert report["fingerprints"]["runtime_guard_sha256"]
    assert report["report_sha256"] != "stale"


def test_v27_protocol_constant_is_ten_days() -> None:
    assert v27.WARMUP_HOURS == 10 * 24
