from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import historical_yield_trend_v31 as v31
from tradebot.research import historical_yield_trend_v311_transport as transport

CASH_SOURCE_POLICY = transport.CASH_SOURCE_POLICY
CASH_TRANSPORT_POLICY = transport.CASH_TRANSPORT_POLICY
FRED_FALLBACK_URL = transport.FRED_FALLBACK_URL
FED_H15_URL = transport.FED_H15_URL
FED_H15_SERIES = transport.FED_H15_SERIES
TRANSPORT_ADDENDUM_PATH = Path(
    "research/V311_FEDERAL_RESERVE_H15_TRANSPORT_ADDENDUM.md"
)

# Compatibility aliases retained for existing callers and tests.
parse_cash_rates_flexible = transport.parse_fred_rates
download_fred_with_retry = transport.download_cash_series_with_resilience
_FRED_TRANSPORT_AUDIT = transport.TRANSPORT_AUDIT
_reset_transport_audit = transport.reset_transport_audit


def run_guarded_overlay(max_workers: int = 16) -> dict[str, Any]:
    if v31.DATA_START != datetime(2017, 9, 1, tzinfo=timezone.utc):
        raise v31.HistoricalYieldTrendV31Error(
            "Corrected v3.1 data start is not active"
        )
    if len(v31.DISCOVERY_PERIODS) != 10:
        raise v31.HistoricalYieldTrendV31Error(
            "Corrected v3.1 discovery must contain 10 quarters"
        )
    if not TRANSPORT_ADDENDUM_PATH.is_file():
        raise v31.HistoricalYieldTrendV31Error(
            f"missing transport addendum: {TRANSPORT_ADDENDUM_PATH}"
        )

    transport.reset_transport_audit()
    original_parser = v31.parse_cash_rates
    original_downloader = v31._download_fred
    v31.parse_cash_rates = transport.parse_fred_rates
    v31._download_fred = transport.download_cash_series_with_resilience
    try:
        report = v31.run_overlay(max_workers=max_workers)
    finally:
        v31.parse_cash_rates = original_parser
        v31._download_fred = original_downloader

    report["cash_source_policy"] = CASH_SOURCE_POLICY
    report["cash_transport_policy"] = CASH_TRANSPORT_POLICY
    report["cash_transport_audit"] = dict(transport.TRANSPORT_AUDIT)
    report["transport_addendum_path"] = str(TRANSPORT_ADDENDUM_PATH)

    fingerprints = dict(report["fingerprints"])
    fingerprints["runner_sha256"] = hashlib.sha256(
        Path(__file__).resolve().read_bytes()
    ).hexdigest()
    fingerprints["cash_transport_sha256"] = hashlib.sha256(
        Path(transport.__file__).resolve().read_bytes()
    ).hexdigest()
    fingerprints["transport_addendum_sha256"] = hashlib.sha256(
        TRANSPORT_ADDENDUM_PATH.read_bytes()
    ).hexdigest()
    report["fingerprints"] = fingerprints

    report.pop("report_sha256", None)
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run guarded v3.1.1 yield-bearing cash trend overlay."
    )
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--max-workers", type=int, default=16)
    args = parser.parse_args(argv)
    report = run_guarded_overlay(max_workers=args.max_workers)
    _atomic_json(Path(args.json_out), report)
    verification = report["verification"]
    print(
        json.dumps(
            {
                "status": report["screening_status"],
                "chosen_model": report["chosen_model"],
                "standard_return": verification["standard"][
                    "net_compounded_return"
                ],
                "stress_return": verification["stress"][
                    "net_compounded_return"
                ],
                "cash_return": verification["standard"][
                    "cash_benchmark_compounded_return"
                ],
                "standard_years": verification["standard"][
                    "window_returns"
                ],
                "stress_years": verification["stress"][
                    "window_returns"
                ],
                "standard_excess_years": verification["standard"][
                    "excess_window_returns"
                ],
                "stress_excess_years": verification["stress"][
                    "excess_window_returns"
                ],
                "maximum_drawdown": verification["standard"][
                    "maximum_drawdown"
                ],
                "cash_source_policy": report["cash_source_policy"],
                "cash_transport_policy": report["cash_transport_policy"],
                "cash_transport_audit": report["cash_transport_audit"],
                "report_sha256": report["report_sha256"],
                "authorizes_trading": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
