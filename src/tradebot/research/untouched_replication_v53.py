from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from tradebot.research import adversarial_alpha_funnel_v52 as v52
from tradebot.research import distributional_utility_v43 as v43
from tradebot.research import dollar_rates_attenuation_v48 as v48
from tradebot.research import yield_bearing_cash_v44 as v44
from tradebot.research import yield_bearing_cash_v44_reproduce as v44_reproduce
from tradebot.research.regime_ranking_v42 import (
    ASSETS,
    STANDARD_ONE_WAY_COST,
    STRESS_ONE_WAY_COST,
    Dataset,
    build_dataset,
    file_sha256,
)
from tradebot.research.regime_ranking_v42_sources import (
    canonical_json,
    load_all_sources,
    utc_iso,
)

SCHEMA_VERSION = "5.3-untouched-nine-month-replication"
PROTOCOL_PATH = Path(
    "research/V53_UNTOUCHED_NINE_MONTH_REPLICATION_PROTOCOL.md"
)
CONTRACT_PATH = Path(
    "research/V531_UNTOUCHED_NINE_MONTH_REPLICATION_CONTRACT.md"
)
V52_REPORT_SHA256 = (
    "60f4d1b88dc0ef66d64a8ec4e192a56"
    "fdaf76a07182bde8e1567f17a61313ab2"
)
V52_COMMIT = "f1bf4e7b9351353bc488aa41415a815ba79cad23"
START = v43.day("2025-10-01")
END = v43.day("2026-06-30")
PANEL_LOOKBACK_DAYS = 230

PRIMARY = v52.Hypothesis(
    family="trend_state",
    source="mean:spot_return_7",
    transform="acceleration",
    history=90,
    lag=10,
    event="cross_up",
    threshold=0.30,
    persistence=7,
    multiplier=0.75,
)

SECONDARY = v52.Hypothesis(
    family="relative_reversal",
    source="positive_breadth:sma_distance_50",
    transform="delta",
    history=20,
    lag=10,
    event="cross_down",
    threshold=0.30,
    persistence=7,
    multiplier=0.75,
)

QUARTERS = (
    ("2025-Q4", v43.day("2025-10-01"), v43.day("2025-12-31")),
    ("2026-Q1", v43.day("2026-01-01"), v43.day("2026-03-31")),
    ("2026-Q2", v43.day("2026-04-01"), v43.day("2026-06-30")),
)


class UntouchedReplicationV53Error(RuntimeError):
    pass


def validate_v52_report(report: dict[str, Any]) -> None:
    if report.get("schema_version") != v52.SCHEMA_VERSION:
        raise UntouchedReplicationV53Error("unexpected v5.2 schema")
    if report.get("report_sha256") != V52_REPORT_SHA256:
        raise UntouchedReplicationV53Error("v5.2 report hash mismatch")
    shortlist = report.get("shortlist", [])
    if len(shortlist) < 2:
        raise UntouchedReplicationV53Error("v5.2 shortlist is incomplete")
    if shortlist[0].get("hypothesis") != asdict(PRIMARY):
        raise UntouchedReplicationV53Error("primary specification mismatch")
    if shortlist[1].get("hypothesis") != asdict(SECONDARY):
        raise UntouchedReplicationV53Error("secondary specification mismatch")
    if report.get("sealed_evaluation_performed") is not False:
        raise UntouchedReplicationV53Error(
            "v5.2 unexpectedly performed sealed evaluation"
        )


def build_evaluation_fold(
    dataset: Dataset,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    cash_history: v44.CashRateHistory,
) -> v52.FoldData:
    panel_start = START - timedelta(days=PANEL_LOOKBACK_DAYS)
    panel_dates = sorted({
        stamp for stamp in dataset.dates
        if panel_start <= stamp <= END
    })
    panel = v52.market_panel(dataset, panel_dates)
    validation_mask = v43.date_mask(dataset, START, END)
    decisions = v43.decisions_by_date(
        dataset, validation_mask, bundle, predictions
    )
    validation_dates, rebalance, selected_rebalance = (
        v52.baseline_schedule(decisions)
    )
    positions = {stamp: index for index, stamp in enumerate(panel_dates)}
    validation_positions = np.asarray(
        [positions[stamp] for stamp in validation_dates], dtype=int
    )
    baseline = v44.simulate(
        dataset,
        validation_mask,
        bundle,
        predictions,
        cash_history,
        one_way_cost=STANDARD_ONE_WAY_COST,
    )
    baseline_daily = np.asarray(baseline["daily_returns"], dtype=float)
    if len(baseline_daily) != len(validation_dates):
        raise UntouchedReplicationV53Error(
            "continuous baseline daily-return length mismatch"
        )
    cash_daily = np.asarray([
        v44.annual_to_daily_rate(
            v44.prior_known_annual_rate(cash_history, stamp)[1]
        )
        for stamp in validation_dates
    ], dtype=float)
    return v52.FoldData(
        name="untouched-2025-10-01-to-2026-06-30",
        panel_dates=panel_dates,
        validation_dates=validation_dates,
        validation_positions=validation_positions,
        panel=panel,
        baseline=baseline,
        baseline_daily_returns=baseline_daily,
        risky_daily_returns=baseline_daily - cash_daily,
        cash_daily_returns=cash_daily,
        rebalance_mask=rebalance,
        selected_rebalance_mask=selected_rebalance,
        bundle=bundle,
        predictions=predictions,
        validation_mask=validation_mask,
    )


def shift_activity(active: np.ndarray, days: int = 1) -> np.ndarray:
    shifted = np.zeros_like(active, dtype=bool)
    if 0 < days < len(active):
        shifted[days:] = active[:-days]
    return shifted


def probabilities_from_activity(
    fold: v52.FoldData,
    active: np.ndarray,
) -> dict[datetime, float]:
    if len(active) != len(fold.validation_dates):
        raise UntouchedReplicationV53Error("activity length mismatch")
    return {
        stamp: (0.0 if bool(active[index]) else 1.0)
        for index, stamp in enumerate(fold.validation_dates)
    }


def simulate_window(
    dataset: Dataset,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    cash_history: v44.CashRateHistory,
    probabilities: dict[datetime, float],
    hypothesis: v52.Hypothesis,
    name: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    mask = v43.date_mask(dataset, start, end)
    standard_baseline = v44.simulate(
        dataset,
        mask,
        bundle,
        predictions,
        cash_history,
        one_way_cost=STANDARD_ONE_WAY_COST,
    )
    stress_baseline = v44.simulate(
        dataset,
        mask,
        bundle,
        predictions,
        cash_history,
        one_way_cost=STRESS_ONE_WAY_COST,
    )
    standard_candidate = v48.simulate_attenuation(
        dataset,
        mask,
        bundle,
        predictions,
        cash_history,
        probabilities,
        0.5,
        hypothesis.multiplier,
        one_way_cost=STANDARD_ONE_WAY_COST,
    )
    stress_candidate = v48.simulate_attenuation(
        dataset,
        mask,
        bundle,
        predictions,
        cash_history,
        probabilities,
        0.5,
        hypothesis.multiplier,
        one_way_cost=STRESS_ONE_WAY_COST,
    )
    days = len({
        dataset.dates[index] for index in np.flatnonzero(mask)
    })
    return {
        "name": name,
        "start": utc_iso(start),
        "end": utc_iso(end),
        "verification_days": days,
        "standard": {
            "baseline": standard_baseline,
            "candidate": standard_candidate,
            "excess_return": float(standard_candidate["net_return"])
            - float(standard_baseline["net_return"]),
        },
        "stress": {
            "baseline": stress_baseline,
            "candidate": stress_candidate,
            "excess_return": float(stress_candidate["net_return"])
            - float(stress_baseline["net_return"]),
        },
    }


def relative_compounded_excess(
    windows: list[dict[str, Any]],
    cost_key: str,
) -> float:
    candidate_growth = float(np.prod([
        1.0 + float(value[cost_key]["candidate"]["net_return"])
        for value in windows
    ]))
    baseline_growth = float(np.prod([
        1.0 + float(value[cost_key]["baseline"]["net_return"])
        for value in windows
    ]))
    return candidate_growth / max(baseline_growth, 1e-12) - 1.0


def summarize_windows(windows: list[dict[str, Any]]) -> dict[str, Any]:
    standard_excess = [
        float(value["standard"]["excess_return"]) for value in windows
    ]
    stress_excess = [
        float(value["stress"]["excess_return"]) for value in windows
    ]
    return {
        "windows": windows,
        "standard_excess_returns": standard_excess,
        "stress_excess_returns": stress_excess,
        "positive_standard_count": sum(value > 0.0 for value in standard_excess),
        "positive_stress_count": sum(value > 0.0 for value in stress_excess),
        "minimum_standard_excess": min(standard_excess),
        "minimum_stress_excess": min(stress_excess),
        "relative_standard_compounded_excess": relative_compounded_excess(
            windows, "standard"
        ),
        "relative_stress_compounded_excess": relative_compounded_excess(
            windows, "stress"
        ),
    }


def evaluate_mechanism(
    dataset: Dataset,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    cash_history: v44.CashRateHistory,
    fold: v52.FoldData,
    hypothesis: v52.Hypothesis,
) -> dict[str, Any]:
    cache: dict[tuple[Any, ...], np.ndarray] = {}
    active = v52.activity_for_fold(fold, hypothesis, cache)
    probabilities = probabilities_from_activity(fold, active)
    continuous = simulate_window(
        dataset,
        bundle,
        predictions,
        cash_history,
        probabilities,
        hypothesis,
        "continuous",
        START,
        END,
    )
    quarters = summarize_windows([
        simulate_window(
            dataset,
            bundle,
            predictions,
            cash_history,
            probabilities,
            hypothesis,
            name,
            start,
            end,
        )
        for name, start, end in QUARTERS
    ])
    sealed = summarize_windows([
        simulate_window(
            dataset,
            bundle,
            predictions,
            cash_history,
            probabilities,
            hypothesis,
            name,
            start,
            end,
        )
        for name, start, end in v43.SEALED_WINDOWS
    ])
    profitability = v48.evaluate_sealed(
        dataset,
        bundle,
        cash_history,
        probabilities,
        0.5,
        hypothesis.multiplier,
    )
    delayed = shift_activity(active, 1)
    delayed_probabilities = probabilities_from_activity(fold, delayed)
    delay_continuous = simulate_window(
        dataset,
        bundle,
        predictions,
        cash_history,
        delayed_probabilities,
        hypothesis,
        "continuous-delay-1",
        START,
        END,
    )
    return {
        "hypothesis": asdict(hypothesis),
        "active_date_count": int(np.sum(active)),
        "active_rebalance_count": int(np.sum(
            active & fold.selected_rebalance_mask
        )),
        "continuous": continuous,
        "quarters": quarters,
        "sealed_windows": sealed,
        "delay_1_continuous": delay_continuous,
        "profitability_evaluation": profitability,
    }


def replication_gates(result: dict[str, Any]) -> dict[str, bool]:
    continuous = result["continuous"]
    standard = continuous["standard"]
    stress = continuous["stress"]
    standard_candidate = standard["candidate"]
    standard_baseline = standard["baseline"]
    stress_candidate = stress["candidate"]
    stress_baseline = stress["baseline"]
    quarters = result["quarters"]
    sealed = result["sealed_windows"]
    delayed_standard_excess = float(
        result["delay_1_continuous"]["standard"]["excess_return"]
    )
    gates = {
        "continuous_standard_excess_positive": (
            float(standard["excess_return"]) > 0.0
        ),
        "continuous_stress_excess_positive": (
            float(stress["excess_return"]) > 0.0
        ),
        "two_of_three_quarters_positive_standard": (
            int(quarters["positive_standard_count"]) >= 2
        ),
        "three_of_five_sealed_windows_positive_standard": (
            int(sealed["positive_standard_count"]) >= 3
        ),
        "quarter_loss_floor": (
            float(quarters["minimum_standard_excess"]) >= -0.0025
        ),
        "sealed_window_loss_floor": (
            float(sealed["minimum_standard_excess"]) >= -0.0025
        ),
        "three_attenuated_decisions": (
            int(standard_candidate["attenuated_decision_count"]) >= 3
        ),
        "standard_drawdown_not_worse_by_25bp": (
            float(standard_candidate["maximum_drawdown"])
            <= float(standard_baseline["maximum_drawdown"]) + 0.0025
        ),
        "stress_drawdown_not_worse_by_25bp": (
            float(stress_candidate["maximum_drawdown"])
            <= float(stress_baseline["maximum_drawdown"]) + 0.0025
        ),
        "standard_actions_not_increased": (
            int(standard_candidate["target_changing_actions"])
            <= int(standard_baseline["target_changing_actions"])
        ),
        "stress_actions_not_increased": (
            int(stress_candidate["target_changing_actions"])
            <= int(stress_baseline["target_changing_actions"])
        ),
        "never_added_asset": bool(
            standard_candidate["never_added_asset"]
            and stress_candidate["never_added_asset"]
        ),
        "never_increased_target": bool(
            standard_candidate["never_increased_target"]
            and stress_candidate["never_increased_target"]
        ),
        "one_day_delay_floor": delayed_standard_excess >= -0.0010,
    }
    return gates


def mechanism_summary(result: dict[str, Any]) -> dict[str, Any]:
    gates = replication_gates(result)
    return {
        **result,
        "replication_gates": gates,
        "untouched_replication_passed": all(gates.values()),
    }


def run_campaign(
    baseline_report: dict[str, Any],
    v52_report: dict[str, Any],
    bundle: v43.Bundle,
    *,
    baseline_bundle_sha256: str | None = None,
    states: dict[str, Any] | None = None,
    source_report: dict[str, Any] | None = None,
    cash_history: v44.CashRateHistory | None = None,
    monthly_workers: int = 24,
    metrics_workers: int = 48,
) -> dict[str, Any]:
    v44_reproduce.validate_baseline_report(baseline_report)
    validate_v52_report(v52_report)
    if states is None:
        states, source_report = load_all_sources(
            monthly_workers=monthly_workers,
            metrics_workers=metrics_workers,
        )
    if source_report is None:
        raise UntouchedReplicationV53Error("source report unavailable")
    if source_report.get("source_end") != "2026-06-30":
        raise UntouchedReplicationV53Error("unexpected source end date")
    dataset = build_dataset(states)
    if cash_history is None:
        cash_history = v44.load_cash_history()
    reproduction = v44_reproduce.run_reproduction(
        baseline_report,
        bundle,
        states=states,
        source_report=source_report,
        cash_history=cash_history,
        baseline_bundle_sha256=baseline_bundle_sha256,
    )
    predictions = v43.predict_components(bundle, dataset.X)
    fold = build_evaluation_fold(
        dataset, bundle, predictions, cash_history
    )
    primary = mechanism_summary(evaluate_mechanism(
        dataset,
        bundle,
        predictions,
        cash_history,
        fold,
        PRIMARY,
    ))
    secondary = mechanism_summary(evaluate_mechanism(
        dataset,
        bundle,
        predictions,
        cash_history,
        fold,
        SECONDARY,
    ))
    baseline_profitability = v44.evaluate_sealed(
        dataset,
        bundle,
        cash_history,
        baseline=reproduction["evaluation"],
    )
    primary_profitability = primary["profitability_evaluation"]
    primary_passed = bool(primary["untouched_replication_passed"])
    deployment_gates = {
        "candidate_untouched_replication": primary_passed,
        "historical_profitability_gates": all(
            bool(value)
            for key, value in primary_profitability["gates"].items()
            if key not in {
                "independent_source_replication",
                "current_market_smoke",
                "untouched_historical_dates",
            }
        ),
        "independent_source_replication": False,
        "current_market_smoke": False,
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_iso(datetime.now(timezone.utc)),
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": primary_passed,
        "retrospective": True,
        "candidate_dates_untouched_before_v53": True,
        "candidate_selection_performed_in_v53": False,
        "candidate_parameters_changed_after_evaluation": False,
        "candidate_period": {
            "start": utc_iso(START),
            "end": utc_iso(END),
            "calendar_quarters": [
                {
                    "name": name,
                    "start": utc_iso(start),
                    "end": utc_iso(end),
                }
                for name, start, end in QUARTERS
            ],
            "sealed_windows": [
                {
                    "name": name,
                    "start": utc_iso(start),
                    "end": utc_iso(end),
                }
                for name, start, end in v43.SEALED_WINDOWS
            ],
        },
        "universe": list(ASSETS),
        "source": source_report,
        "cash_source": cash_history.source,
        "runtime": v48.runtime_versions(),
        "protocol_sha256": file_sha256(PROTOCOL_PATH),
        "implementation_contract_sha256": file_sha256(CONTRACT_PATH),
        "implementation_sha256": file_sha256(Path(__file__).resolve()),
        "v52_report_sha256": v52_report["report_sha256"],
        "v52_implementation_commit": V52_COMMIT,
        "baseline_report_sha256": baseline_report["report_sha256"],
        "baseline_bundle_sha256": baseline_bundle_sha256,
        "v44_reproduction_report_sha256": reproduction["report_sha256"],
        "reproduction": {
            **reproduction["reproduction"],
            "final_bundle_training_end": utc_iso(v43.TRAIN_END),
            "candidate_start_after_v52_discovery_end": True,
            "primary_specification_exact": (
                primary["hypothesis"] == asdict(PRIMARY)
            ),
            "secondary_specification_exact": (
                secondary["hypothesis"] == asdict(SECONDARY)
            ),
            "candidate_retrained": False,
            "candidate_recalibrated": False,
            "candidate_substituted": False,
        },
        "baseline_profitability_evaluation": baseline_profitability,
        "primary": primary,
        "secondary_corroboration": secondary,
        "deployment_gates": deployment_gates,
        "accepted_strategy_remains": "v4.4-yield-bearing-cash",
        "status": (
            "UNTOUCHED_MECHANISM_REPLICATION_PASSED_"
            "PENDING_INDEPENDENT_SOURCE_AND_CURRENT_SMOKE"
            if primary_passed
            else "UNTOUCHED_MECHANISM_REPLICATION_FAILED"
        ),
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run frozen v5.3 untouched nine-month replication"
    )
    parser.add_argument("--baseline-json", type=Path, required=True)
    parser.add_argument("--v52-json", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("evidence/v53/untouched.json"),
    )
    parser.add_argument("--monthly-workers", type=int, default=24)
    parser.add_argument("--metrics-workers", type=int, default=48)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    baseline_report = json.loads(
        args.baseline_json.read_text(encoding="utf-8")
    )
    v52_report = json.loads(
        args.v52_json.read_text(encoding="utf-8")
    )
    bundle = v44_reproduce.load_bundle(args.bundle)
    report = run_campaign(
        baseline_report,
        v52_report,
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
    primary = report["primary"]
    profitability = primary["profitability_evaluation"]
    print(json.dumps({
        "status": report["status"],
        "untouched_replication_passed": primary[
            "untouched_replication_passed"
        ],
        "continuous_standard_excess": primary["continuous"][
            "standard"
        ]["excess_return"],
        "continuous_stress_excess": primary["continuous"][
            "stress"
        ]["excess_return"],
        "positive_quarters": primary["quarters"][
            "positive_standard_count"
        ],
        "positive_sealed_windows": primary["sealed_windows"][
            "positive_standard_count"
        ],
        "aggregate_standard_return": profitability[
            "aggregate_standard_return"
        ],
        "annualized_standard_return": profitability[
            "annualized_standard_return"
        ],
        "historical_profitability_status": profitability["status"],
        "secondary_untouched_replication_passed": report[
            "secondary_corroboration"
        ]["untouched_replication_passed"],
        "report_sha256": report["report_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
