from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from tradebot.research import distributional_utility_v43 as v43
from tradebot.research import residual_momentum_breakout_v47 as core
from tradebot.research import yield_bearing_cash_v44 as v44
from tradebot.research import yield_bearing_cash_v44_reproduce as v44_reproduce
from tradebot.research.regime_ranking_v42 import build_dataset, file_sha256
from tradebot.research.regime_ranking_v42_sources import (
    canonical_json,
    load_all_sources,
    utc_iso,
)


def filter_to_dates(
    dataset: core.ResidualDataset,
    allowed_dates: set[datetime],
) -> core.ResidualDataset:
    mask = np.asarray(
        [stamp in allowed_dates for stamp in dataset.dates],
        dtype=bool,
    )
    values: dict[str, Any] = {}
    for field in fields(core.ResidualDataset):
        value = getattr(dataset, field.name)
        if field.name == "dates":
            values[field.name] = [
                stamp for stamp, keep in zip(dataset.dates, mask, strict=True)
                if keep
            ]
        else:
            values[field.name] = value[mask]
    return core.ResidualDataset(**values)


def run_campaign(
    baseline_report: dict[str, Any],
    final_bundle: v43.Bundle,
    states: dict[str, Any] | None = None,
    source_report: dict[str, Any] | None = None,
    cash_history: v44.CashRateHistory | None = None,
    *,
    baseline_bundle_sha256: str | None = None,
    monthly_workers: int = 24,
    metrics_workers: int = 48,
) -> dict[str, Any]:
    if states is None:
        states, source_report = load_all_sources(
            monthly_workers=monthly_workers,
            metrics_workers=metrics_workers,
        )
    if cash_history is None:
        cash_history = v44.load_cash_history()

    report = core.run_campaign(
        baseline_report,
        final_bundle,
        states=states,
        source_report=source_report,
        cash_history=cash_history,
        baseline_bundle_sha256=baseline_bundle_sha256,
        monthly_workers=monthly_workers,
        metrics_workers=metrics_workers,
    )
    reference_dataset = build_dataset(states)
    residual_dataset = core.build_residual_dataset(states)
    exact_dataset = filter_to_dates(
        residual_dataset,
        set(reference_dataset.dates),
    )
    selected = core.Config(**report["selection"]["selected_config"])
    report["evaluation"] = core.evaluate_sealed(
        exact_dataset,
        cash_history,
        selected,
        v44_baseline=report["v44_baseline"],
    )
    report["residual_dataset"]["verification_alignment"] = (
        "exact-frozen-v43-decision-dates"
    )
    report["residual_dataset"]["aligned_date_count"] = len(
        exact_dataset.dates
    )
    report["generated_at_utc"] = utc_iso(datetime.now(timezone.utc))
    report.pop("report_sha256", None)
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run exact-date v4.7 residual momentum research"
    )
    parser.add_argument("--baseline-json", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("evidence/v47/historical.json"),
    )
    parser.add_argument("--monthly-workers", type=int, default=24)
    parser.add_argument("--metrics-workers", type=int, default=48)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    baseline_report = json.loads(
        args.baseline_json.read_text(encoding="utf-8")
    )
    bundle = v44_reproduce.load_bundle(args.bundle)
    report = run_campaign(
        baseline_report,
        bundle,
        baseline_bundle_sha256=file_sha256(args.bundle),
        monthly_workers=max(1, args.monthly_workers),
        metrics_workers=max(1, args.metrics_workers),
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    evaluation = report["evaluation"]
    print(json.dumps({
        "status": evaluation["status"],
        "report_sha256": report["report_sha256"],
        "selected_config": report["selection"]["selected_config"],
        "selected_eligible": report["selection"]["selected_eligible"],
        "eligible_candidate_count": report["selection"][
            "eligible_candidate_count"
        ],
        "verification_days": evaluation["verification_days"],
        "standard_return": evaluation["aggregate_standard_return"],
        "stress_return": evaluation["aggregate_stress_return"],
        "annualized_standard_return": evaluation[
            "annualized_standard_return"
        ],
        "maximum_drawdown": evaluation["maximum_drawdown"],
        "mean_v44_daily_return_correlation": evaluation[
            "mean_v44_daily_return_correlation"
        ],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
