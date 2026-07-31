from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from datetime import datetime
from pathlib import Path

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import historical_discovery_v26 as v26
from tradebot.research import historical_discovery_v27 as v27
from tradebot.research import historical_proxy_screen_v25 as v25


METRICS_ROW_POLICY = "skip_nonpositive_observations_require_positive_hour"
_METRICS_AUDIT = {
    "skipped_nonpositive_observations": 0,
    "positive_observations": 0,
    "positive_hours": 0,
}


def _reset_metrics_audit() -> None:
    for key in _METRICS_AUDIT:
        _METRICS_AUDIT[key] = 0


def _parse_metrics_positive_only(
    archive: v25.DownloadedArchive,
) -> dict[datetime, float]:
    """Keep the latest positive OI observation per hour; skip invalid nonpositive rows."""
    rows = v25._csv_rows(archive)
    if not v25._looks_like_header(rows[0]):
        raise v25.HistoricalProxyScreenError(
            f"Metrics archive has no header: {archive.url}"
        )
    header = v25._header_map(rows[0])
    time_index = v25._find_column(header, ("create_time", "timestamp", "time"))
    oi_index = v25._find_column(
        header,
        ("sum_open_interest", "open_interest", "openinterest"),
    )
    if time_index is None or oi_index is None:
        raise v25.HistoricalProxyScreenError(
            f"Metrics columns are unavailable in {archive.url}"
        )

    latest: dict[datetime, tuple[datetime, float]] = {}
    for row in rows[1:]:
        if max(time_index, oi_index) >= len(row):
            continue
        observed = v25._timestamp(row[time_index], "metrics.time")
        value = v25._finite(
            row[oi_index],
            "metrics.open_interest",
            positive=False,
        )
        if value <= 0.0:
            _METRICS_AUDIT["skipped_nonpositive_observations"] += 1
            continue
        _METRICS_AUDIT["positive_observations"] += 1
        hour = observed.replace(minute=0, second=0, microsecond=0)
        prior = latest.get(hour)
        if prior is None or observed > prior[0]:
            latest[hour] = (observed, value)

    parsed = {hour: value for hour, (_, value) in latest.items()}
    _METRICS_AUDIT["positive_hours"] += len(parsed)
    return parsed


def run_guarded_discovery(max_workers: int = 20) -> dict[str, object]:
    """Run v2.7 with its frozen warm-up and fail-closed metrics-row policy."""
    v26.WARMUP_HOURS = v27.WARMUP_HOURS
    if v26.WARMUP_HOURS != 10 * 24:
        raise RuntimeError("v2.7 effective warm-up must be exactly 240 hours")

    _reset_metrics_audit()
    original_metrics_parser = v26._parse_metrics
    v26._parse_metrics = _parse_metrics_positive_only
    try:
        report = v27.run_discovery(max_workers=max_workers)
    finally:
        v26._parse_metrics = original_metrics_parser

    report["effective_state_assembly_warmup_hours"] = v26.WARMUP_HOURS
    report["metrics_row_policy"] = METRICS_ROW_POLICY
    report["metrics_row_audit"] = dict(_METRICS_AUDIT)
    fingerprints = dict(report["fingerprints"])
    fingerprints["runtime_guard_sha256"] = hashlib.sha256(
        Path(__file__).resolve().read_bytes()
    ).hexdigest()
    fingerprints["metrics_parser_sha256"] = hashlib.sha256(
        inspect.getsource(_parse_metrics_positive_only).encode("utf-8")
    ).hexdigest()
    report["fingerprints"] = fingerprints
    report.pop("report_sha256", None)
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run v2.7 with frozen warm-up and metrics-row guards."
    )
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--max-workers", type=int, default=20)
    args = parser.parse_args(argv)
    report = run_guarded_discovery(max_workers=args.max_workers)
    _atomic_json(Path(args.json_out), report)
    primary = report["results"]["8"]["standard"]
    print(
        json.dumps(
            {
                "status": report["screening_status"],
                "events": report["event_count"],
                "validation_events": report["validation_event_count"],
                "validation_returns": {
                    name: primary["window_returns"][name]
                    for name in v27.VALIDATION_WINDOWS
                },
                "eight_hour_net_return": primary["net_compounded_return"],
                "eight_hour_stress_return": report["results"]["8"]["stress"]["net_compounded_return"],
                "effective_state_assembly_warmup_hours": report[
                    "effective_state_assembly_warmup_hours"
                ],
                "metrics_row_policy": report["metrics_row_policy"],
                "metrics_row_audit": report["metrics_row_audit"],
                "report_sha256": report["report_sha256"],
                "paper_only": True,
                "authorizes_trading": False,
                "authorizes_shadow_paper": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
