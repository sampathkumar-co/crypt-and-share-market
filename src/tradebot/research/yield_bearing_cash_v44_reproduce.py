from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradebot.research import distributional_utility_v43 as v43
from tradebot.research import yield_bearing_cash_v44 as v44
from tradebot.research.regime_ranking_v42 import (
    ASSETS,
    build_dataset,
    file_sha256,
)
from tradebot.research.regime_ranking_v42_sources import (
    canonical_json,
    load_all_sources,
    utc_iso,
)


class YieldBearingCashV44ReproductionError(v44.YieldBearingCashV44Error):
    pass


def validate_baseline_report(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("schema_version") != v43.SCHEMA_VERSION:
        raise YieldBearingCashV44ReproductionError(
            "baseline report is not a v4.3 distributional-utility report"
        )
    if report.get("paper_only") is not True:
        raise YieldBearingCashV44ReproductionError(
            "baseline report is not paper-only"
        )
    if report.get("authorizes_trading") is not False:
        raise YieldBearingCashV44ReproductionError(
            "baseline report authorizes trading"
        )
    required = {
        "bundle",
        "calibration",
        "dataset",
        "evaluation",
        "report_sha256",
        "source",
    }
    missing = sorted(required - set(report))
    if missing:
        raise YieldBearingCashV44ReproductionError(
            f"baseline report is missing fields: {missing}"
        )
    payload = dict(report)
    claimed = str(payload.pop("report_sha256"))
    computed = hashlib.sha256(
        canonical_json(payload).encode("utf-8")
    ).hexdigest()
    if claimed != computed:
        raise YieldBearingCashV44ReproductionError(
            "baseline report SHA-256 does not match its contents"
        )
    return report


def load_bundle(path: Path) -> v43.Bundle:
    import joblib

    try:
        raw = joblib.load(path)
    except Exception as exc:  # pragma: no cover - library-specific failures
        raise YieldBearingCashV44ReproductionError(
            f"unable to load baseline bundle: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise YieldBearingCashV44ReproductionError(
            "baseline bundle payload is not a mapping"
        )
    if raw.get("schema_version") != v43.SCHEMA_VERSION:
        raise YieldBearingCashV44ReproductionError(
            "baseline bundle schema does not match v4.3"
        )
    if raw.get("authorizes_trading") is not False:
        raise YieldBearingCashV44ReproductionError(
            "baseline bundle authorizes trading"
        )
    state = raw.get("bundle")
    if not isinstance(state, dict):
        raise YieldBearingCashV44ReproductionError(
            "baseline bundle state is unavailable"
        )
    return v43.bundle_from_state(state)


def run_reproduction(
    baseline_report: dict[str, Any],
    bundle: v43.Bundle,
    states: dict[str, Any] | None = None,
    source_report: dict[str, Any] | None = None,
    cash_history: v44.CashRateHistory | None = None,
    *,
    baseline_bundle_sha256: str | None = None,
    monthly_workers: int = 24,
    metrics_workers: int = 48,
) -> dict[str, Any]:
    validate_baseline_report(baseline_report)
    if states is None:
        states, source_report = load_all_sources(
            monthly_workers=monthly_workers,
            metrics_workers=metrics_workers,
        )
    if source_report is None:
        raise YieldBearingCashV44ReproductionError(
            "current source report is unavailable"
        )
    if canonical_json(source_report) != canonical_json(
        baseline_report["source"]
    ):
        raise YieldBearingCashV44ReproductionError(
            "current source inventory differs from the frozen v4.3 report"
        )

    dataset = build_dataset(states)
    expected_dataset = baseline_report["dataset"]
    observed_dataset = {
        "row_count": len(dataset.X),
        "date_count": len(set(dataset.dates)),
        "first_date": utc_iso(min(dataset.dates)),
        "last_date": utc_iso(max(dataset.dates)),
        "feature_count": len(dataset.feature_names),
        "training_end": utc_iso(v43.TRAIN_END),
        "calibration_start": utc_iso(v43.CALIBRATION_START),
        "calibration_end": utc_iso(v43.CALIBRATION_END),
    }
    if canonical_json(observed_dataset) != canonical_json(expected_dataset):
        raise YieldBearingCashV44ReproductionError(
            "current dataset metadata differs from the frozen v4.3 report"
        )

    bundle_summary = v43.bundle_summary(bundle)
    if canonical_json(bundle_summary) != canonical_json(
        baseline_report["bundle"]
    ):
        raise YieldBearingCashV44ReproductionError(
            "provided bundle does not match the frozen v4.3 report"
        )

    recomputed_baseline = v43.evaluate_sealed(dataset, bundle)
    if canonical_json(recomputed_baseline) != canonical_json(
        baseline_report["evaluation"]
    ):
        raise YieldBearingCashV44ReproductionError(
            "provided bundle does not exactly reproduce v4.3 evaluation"
        )

    if cash_history is None:
        cash_history = v44.load_cash_history()
    if min(cash_history.annual_rates) > min(dataset.dates):
        raise YieldBearingCashV44ReproductionError(
            "cash history starts after the research dataset"
        )

    evaluation = v44.evaluate_sealed(
        dataset,
        bundle,
        cash_history,
        baseline=recomputed_baseline,
    )
    comparison = dict(evaluation["v43_comparison"])
    report: dict[str, Any] = {
        "schema_version": v44.SCHEMA_VERSION,
        "generated_at_utc": utc_iso(datetime.now(timezone.utc)),
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": True,
        "retrospective": True,
        "untouched_historical_dates": False,
        "universe": list(ASSETS),
        "source": source_report,
        "cash_source": cash_history.source,
        "runtime": v44.runtime_versions(),
        "dataset": observed_dataset,
        "protocol_sha256": file_sha256(v44.PROTOCOL_PATH),
        "implementation_contract_sha256": file_sha256(v44.CONTRACT_PATH),
        "v43_protocol_sha256": file_sha256(v43.PROTOCOL_PATH),
        "v43_implementation_contract_sha256": file_sha256(
            v43.CONTRACT_PATH
        ),
        "baseline_report_sha256": baseline_report["report_sha256"],
        "baseline_bundle_sha256": baseline_bundle_sha256,
        "bundle": bundle_summary,
        "calibration": baseline_report["calibration"],
        "v43_baseline": recomputed_baseline,
        "evaluation": evaluation,
        "comparison_with_v43": comparison,
        "reproduction": {
            "source_inventory_exact": True,
            "dataset_metadata_exact": True,
            "bundle_summary_exact": True,
            "v43_evaluation_exact": True,
            "v43_retrained_for_overlay": False,
        },
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Reproduce retrospective v4.4 from a frozen v4.3 report and bundle"
        )
    )
    parser.add_argument("--baseline-json", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("evidence/v44/historical.json"),
    )
    parser.add_argument("--monthly-workers", type=int, default=24)
    parser.add_argument("--metrics-workers", type=int, default=48)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    baseline_report = json.loads(
        args.baseline_json.read_text(encoding="utf-8")
    )
    bundle = load_bundle(args.bundle)
    report = run_reproduction(
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
    comparison = report["comparison_with_v43"]
    print(json.dumps({
        "status": evaluation["status"],
        "report_sha256": report["report_sha256"],
        "standard_return": evaluation["aggregate_standard_return"],
        "stress_return": evaluation["aggregate_stress_return"],
        "annualized_standard_return": evaluation[
            "annualized_standard_return"
        ],
        "cash_contribution": evaluation["cash_contribution"],
        "standard_return_uplift": comparison["standard_return_uplift"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
