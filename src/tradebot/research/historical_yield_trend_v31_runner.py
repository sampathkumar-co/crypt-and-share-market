from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import historical_yield_trend_v31 as v31

CASH_SOURCE_POLICY = (
    "fred_dgs3mo_accept_DATE_or_observation_date_skip_only_missing_values"
)


def parse_cash_rates_flexible(content: bytes) -> dict[datetime, float]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise v31.HistoricalYieldTrendV31Error(
            "FRED CSV is not UTF-8"
        ) from exc
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = list(reader.fieldnames or [])
    date_column = (
        "observation_date"
        if "observation_date" in fieldnames
        else "DATE"
        if "DATE" in fieldnames
        else None
    )
    if date_column is None or "DGS3MO" not in fieldnames:
        raise v31.HistoricalYieldTrendV31Error(
            f"FRED CSV columns unavailable: {fieldnames}"
        )
    rates: dict[datetime, float] = {}
    for row in reader:
        raw = str(row.get("DGS3MO", "")).strip()
        if not raw or raw == ".":
            continue
        try:
            # Parse the published decimal text exactly before converting the
            # percentage to a fraction. This avoids binary multiplication
            # artifacts such as 1.10 * 0.01 becoming 0.011000000000000001.
            value = float(Decimal(raw) / Decimal("100"))
            day = datetime.fromisoformat(
                str(row[date_column])
            ).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError, InvalidOperation) as exc:
            raise v31.HistoricalYieldTrendV31Error(
                f"Invalid FRED row: {row}"
            ) from exc
        if value <= -1.0:
            raise v31.HistoricalYieldTrendV31Error(
                f"Invalid annual cash rate on {v31._utc(day)}: {value}"
            )
        rates[day] = value
    if not rates:
        raise v31.HistoricalYieldTrendV31Error(
            "FRED CSV contains no valid rates"
        )
    return rates


def run_guarded_overlay(max_workers: int = 16) -> dict[str, Any]:
    if v31.DATA_START != datetime(2017, 9, 1, tzinfo=timezone.utc):
        raise v31.HistoricalYieldTrendV31Error(
            "Corrected v3.1 data start is not active"
        )
    if len(v31.DISCOVERY_PERIODS) != 10:
        raise v31.HistoricalYieldTrendV31Error(
            "Corrected v3.1 discovery must contain 10 quarters"
        )
    original = v31.parse_cash_rates
    v31.parse_cash_rates = parse_cash_rates_flexible
    try:
        report = v31.run_overlay(max_workers=max_workers)
    finally:
        v31.parse_cash_rates = original
    report["cash_source_policy"] = CASH_SOURCE_POLICY
    fingerprints = dict(report["fingerprints"])
    fingerprints["runner_sha256"] = hashlib.sha256(
        Path(__file__).resolve().read_bytes()
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
        description="Run guarded v3.1 yield-bearing cash trend overlay."
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
                "maximum_drawdown": verification["standard"][
                    "maximum_drawdown"
                ],
                "cash_source_policy": report["cash_source_policy"],
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
