from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone

import pytest

from tradebot.research import historical_discovery_v26 as v26
from tradebot.research import historical_discovery_v27 as v27
from tradebot.research import historical_discovery_v27_runner as runner
from tradebot.research import historical_proxy_screen_v25 as v25


def _metrics_archive(csv_text: str) -> v25.DownloadedArchive:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("metrics.csv", csv_text)
    return v25.DownloadedArchive(
        url="https://example.invalid/metrics.zip",
        sha256="test",
        content=buffer.getvalue(),
    )


def test_guard_sets_exact_protocol_warmup_and_parser(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_discovery(*, max_workers: int):
        captured["workers"] = max_workers
        captured["warmup"] = v26.WARMUP_HOURS
        captured["parser_is_patched"] = (
            v26._parse_metrics is runner._parse_metrics_positive_only
        )
        return {
            "fingerprints": {
                "implementation_sha256": "a",
                "protocol_sha256": "b",
            },
            "report_sha256": "stale",
        }

    monkeypatch.setattr(v27, "run_discovery", fake_run_discovery)
    original_warmup = v26.WARMUP_HOURS
    original_parser = v26._parse_metrics
    try:
        report = runner.run_guarded_discovery(max_workers=7)
    finally:
        v26.WARMUP_HOURS = original_warmup

    assert captured == {
        "workers": 7,
        "warmup": 240,
        "parser_is_patched": True,
    }
    assert v26._parse_metrics is original_parser
    assert report["effective_state_assembly_warmup_hours"] == 240
    assert report["metrics_row_policy"] == (
        "skip_nonpositive_observations_require_positive_hour"
    )
    assert report["metrics_row_audit"] == {
        "skipped_nonpositive_observations": 0,
        "positive_observations": 0,
        "positive_hours": 0,
    }
    assert report["fingerprints"]["runtime_guard_sha256"]
    assert report["fingerprints"]["metrics_parser_sha256"]
    assert report["report_sha256"] != "stale"


def test_metrics_parser_skips_nonpositive_rows_and_keeps_latest_positive() -> None:
    archive = _metrics_archive(
        "create_time,sum_open_interest\n"
        "2024-01-01T00:05:00Z,0\n"
        "2024-01-01T00:10:00Z,-2\n"
        "2024-01-01T00:20:00Z,100\n"
        "2024-01-01T00:55:00Z,120\n"
        "2024-01-01T01:05:00Z,0\n"
    )
    runner._reset_metrics_audit()

    parsed = runner._parse_metrics_positive_only(archive)

    assert parsed == {
        datetime(2024, 1, 1, 0, tzinfo=timezone.utc): 120.0,
    }
    assert runner._METRICS_AUDIT == {
        "skipped_nonpositive_observations": 3,
        "positive_observations": 2,
        "positive_hours": 1,
    }


def test_metrics_parser_still_rejects_malformed_values() -> None:
    archive = _metrics_archive(
        "create_time,sum_open_interest\n"
        "2024-01-01T00:05:00Z,not-a-number\n"
    )
    runner._reset_metrics_audit()

    with pytest.raises(v25.HistoricalProxyScreenError):
        runner._parse_metrics_positive_only(archive)


def test_v27_protocol_constant_is_ten_days() -> None:
    assert v27.WARMUP_HOURS == 10 * 24
