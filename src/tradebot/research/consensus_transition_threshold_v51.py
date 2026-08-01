from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from tradebot.research import distributional_utility_v43 as v43
from tradebot.research import dollar_rates_attenuation_v48 as v48
from tradebot.research import fresh_macro_transition_v50 as v50
from tradebot.research import macro_liquidity_state_v47 as v47
from tradebot.research import walk_forward_selective_veto_v46 as v46
from tradebot.research import yield_bearing_cash_v44 as v44
from tradebot.research import yield_bearing_cash_v44_reproduce as v44_reproduce
from tradebot.research.regime_ranking_v42 import (
    ASSETS,
    STANDARD_ONE_WAY_COST,
    Dataset,
    build_dataset,
    file_sha256,
)
from tradebot.research.regime_ranking_v42_sources import (
    canonical_json,
    load_all_sources,
    utc_iso,
)

SCHEMA_VERSION = "5.1-consensus-transition-threshold"
PROTOCOL_PATH = Path(
    "research/V51_CONSENSUS_TRANSITION_THRESHOLD_PROTOCOL.md"
)
CONTRACT_PATH = Path(
    "research/V511_CONSENSUS_TRANSITION_THRESHOLD_IMPLEMENTATION_CONTRACT.md"
)
FAMILY = "fresh_14d"
WINDOW_DAYS = 14
ACTIVE_MULTIPLIER = 0.50
THRESHOLD_GRID = v50.STATE_THRESHOLDS
EXPECTED_THRESHOLD_COUNT = 6
EXPECTED_V50_THRESHOLDS = (0.60, 0.55, 0.65, 0.65, 0.60, 0.50)
EXPECTED_V50_FOLD_EXCESS = (
    0.00025947595841269155,
    0.0004453652650617812,
    0.0,
    0.0,
    0.000006916900424425165,
    0.002655854486571352,
)
V50_REPORT_SHA256 = (
    "8e9affe745095b3a654a9f2ee18003085"
    "beb1c7a372ebd19de2e5b3d9f958dd0"
)
V50_FINAL_STANDARD_RETURN = 0.027738928925329143
V50_FINAL_STRESS_RETURN = 0.02480956973854065
V50_FINAL_ANNUALIZED_RETURN = 0.038405274219430074


class ConsensusTransitionThresholdV51Error(RuntimeError):
    pass


@dataclass(frozen=True)
class FixedThresholdFoldResult:
    fold: str
    threshold: float
    training_date_count: int
    positive_label_share: float
    validation_baseline: dict[str, Any]
    validation_transition: dict[str, Any]
    validation_excess: float


def consensus_threshold(
    thresholds: list[float] | tuple[float, ...],
) -> dict[str, Any]:
    values = [float(value) for value in thresholds]
    if len(values) != EXPECTED_THRESHOLD_COUNT:
        raise ConsensusTransitionThresholdV51Error(
            "consensus requires exactly six fold thresholds"
        )
    if any(value not in THRESHOLD_GRID for value in values):
        raise ConsensusTransitionThresholdV51Error(
            "consensus input contains an off-grid threshold"
        )
    ordered = sorted(values)
    raw_median = float(statistics.median(ordered))
    if raw_median in THRESHOLD_GRID:
        selected = raw_median
    else:
        higher = [value for value in THRESHOLD_GRID if value > raw_median]
        if not higher:
            raise ConsensusTransitionThresholdV51Error(
                "median cannot be rounded upward on the frozen grid"
            )
        selected = min(higher)
    return {
        "chronological_thresholds": values,
        "sorted_thresholds": ordered,
        "raw_median": raw_median,
        "consensus_threshold": float(selected),
        "threshold_grid": list(THRESHOLD_GRID),
        "rounding_rule": "ordinary median; off-grid midpoint rounds upward",
    }


def evaluate_fold_fixed_threshold(
    dataset: Dataset,
    macro_by_date: dict[datetime, np.ndarray],
    cash_history: v44.CashRateHistory,
    fold: dict[str, Any],
    threshold: float,
) -> FixedThresholdFoldResult:
    spec = fold["spec"]
    X_train, y_train, training_dates = v47.date_level_samples(
        dataset,
        macro_by_date,
        v50.FAMILY,
        start=None,
        end=spec.training_end,
    )
    macro_model = v47.fit_macro_classifier(X_train, y_train)
    probabilities = v47.probability_by_date(
        macro_model,
        macro_by_date,
        v50.FAMILY,
    )
    active_states, crossings = v50.transition_active_by_date(
        probabilities,
        threshold,
        WINDOW_DAYS,
    )
    transition = v50.simulate_transition(
        dataset,
        fold["validation_mask"],
        fold["bundle"],
        fold["predictions"],
        cash_history,
        active_states,
        crossings,
        FAMILY,
        threshold,
        one_way_cost=STANDARD_ONE_WAY_COST,
    )
    return FixedThresholdFoldResult(
        fold=spec.name,
        threshold=threshold,
        training_date_count=len(training_dates),
        positive_label_share=float(np.mean(y_train)),
        validation_baseline=fold["baseline"],
        validation_transition=transition,
        validation_excess=float(transition["net_return"])
        - float(fold["baseline"]["net_return"]),
    )


def transition_results_for_eligibility(
    results: list[FixedThresholdFoldResult],
) -> list[v50.TransitionFoldResult]:
    return [
        v50.TransitionFoldResult(
            fold=value.fold,
            transition_family=FAMILY,
            threshold=value.threshold,
            training_date_count=value.training_date_count,
            positive_label_share=value.positive_label_share,
            calibration_months=[],
            calibration_minimum_excess=0.0,
            calibration_compounded_excess=0.0,
            validation_baseline=value.validation_baseline,
            validation_transition=value.validation_transition,
            validation_excess=value.validation_excess,
        )
        for value in results
    ]


def fixed_threshold_audit(
    results: list[FixedThresholdFoldResult],
) -> dict[str, Any]:
    compatible = transition_results_for_eligibility(results)
    eligible, reasons = v50.family_eligibility(FAMILY, compatible)
    return {
        "eligible": eligible,
        "ineligibility_reasons": reasons,
        "minimum_fold_excess": min(
            value.validation_excess for value in results
        ),
        "positive_excess_fold_count": sum(
            value.validation_excess > 0.0 for value in results
        ),
        "compounded_excess": v50.compounded_validation_excess(
            compatible
        ),
        "attenuated_decision_count": sum(
            int(value.validation_transition["attenuated_decision_count"])
            for value in results
        ),
        "folds": [asdict(value) for value in results],
    }


def fit_final_states_and_audit(
    dataset: Dataset,
    macro_by_date: dict[datetime, np.ndarray],
    cash_history: v44.CashRateHistory,
    bundle: v43.Bundle,
    threshold: float,
) -> tuple[dict[datetime, bool], dict[datetime, bool], dict[str, Any]]:
    X_train, y_train, training_dates = v47.date_level_samples(
        dataset,
        macro_by_date,
        v50.FAMILY,
        start=None,
        end=v43.TRAIN_END,
    )
    macro_model = v47.fit_macro_classifier(X_train, y_train)
    probabilities = v47.probability_by_date(
        macro_model,
        macro_by_date,
        v50.FAMILY,
    )
    active_states, crossings = v50.transition_active_by_date(
        probabilities,
        threshold,
        WINDOW_DAYS,
    )
    predictions = v43.predict_components(bundle, dataset.X)
    months: list[dict[str, Any]] = []
    for name, start, end in v48.calendar_month_blocks(
        v43.CALIBRATION_START,
        v43.CALIBRATION_END,
    ):
        mask = v43.date_mask(dataset, start, end)
        baseline = v44.simulate(
            dataset,
            mask,
            bundle,
            predictions,
            cash_history,
            one_way_cost=STANDARD_ONE_WAY_COST,
        )
        transition = v50.simulate_transition(
            dataset,
            mask,
            bundle,
            predictions,
            cash_history,
            active_states,
            crossings,
            FAMILY,
            threshold,
            one_way_cost=STANDARD_ONE_WAY_COST,
        )
        months.append({
            "name": name,
            "start": utc_iso(start),
            "end": utc_iso(end),
            "baseline": baseline,
            "transition": transition,
            "excess": float(transition["net_return"])
            - float(baseline["net_return"]),
        })
    return active_states, crossings, {
        "audit_only": True,
        "used_for_selection": False,
        "transition_family": FAMILY,
        "threshold": threshold,
        "window_days": WINDOW_DAYS,
        "multiplier": ACTIVE_MULTIPLIER,
        "training_end": utc_iso(v43.TRAIN_END),
        "training_date_count": len(training_dates),
        "positive_label_share": float(np.mean(y_train)),
        "months": months,
        "minimum_monthly_excess": min(
            float(value["excess"]) for value in months
        ),
        "compounded_excess": v48._compounded_excess(
            [value["transition"] for value in months],
            [value["baseline"] for value in months],
        ),
        "attenuated_decision_count": sum(
            int(value["transition"]["attenuated_decision_count"])
            for value in months
        ),
    }


def disabled_final_states(
    dataset: Dataset,
) -> tuple[dict[datetime, bool], dict[datetime, bool], dict[str, Any]]:
    dates = set(dataset.dates)
    return (
        {stamp: False for stamp in dates},
        {stamp: False for stamp in dates},
        {
            "audit_only": True,
            "used_for_selection": False,
            "transition_family": v50.DISABLED_FAMILY,
            "threshold": None,
            "window_days": None,
            "multiplier": 1.0,
            "training_end": utc_iso(v43.TRAIN_END),
            "training_date_count": 0,
            "positive_label_share": None,
            "months": [],
            "minimum_monthly_excess": 0.0,
            "compounded_excess": 0.0,
            "attenuated_decision_count": 0,
        },
    )


def run_campaign(
    baseline_report: dict[str, Any],
    bundle: v43.Bundle,
    *,
    baseline_bundle_sha256: str | None = None,
    states: dict[str, Any] | None = None,
    source_report: dict[str, Any] | None = None,
    cash_history: v44.CashRateHistory | None = None,
    macro_history: v47.MacroHistory | None = None,
    monthly_workers: int = 24,
    metrics_workers: int = 48,
) -> dict[str, Any]:
    v44_reproduce.validate_baseline_report(baseline_report)
    if states is None:
        states, source_report = load_all_sources(
            monthly_workers=monthly_workers,
            metrics_workers=metrics_workers,
        )
    if source_report is None:
        raise ConsensusTransitionThresholdV51Error(
            "source report is unavailable"
        )
    dataset = build_dataset(states)
    if cash_history is None:
        cash_history = v44.load_cash_history()
    v44_report = v44_reproduce.run_reproduction(
        baseline_report,
        bundle,
        states=states,
        source_report=source_report,
        cash_history=cash_history,
        baseline_bundle_sha256=baseline_bundle_sha256,
    )
    if macro_history is None:
        macro_history = v47.load_macro_history()
    macro_matrix, macro_by_date = v47.build_macro_matrix(
        dataset,
        macro_history,
    )
    folds = v46.build_walk_forward_folds(dataset, cash_history)
    original_results = [
        v50.fit_and_evaluate_fold(
            dataset,
            macro_by_date,
            cash_history,
            fold,
            FAMILY,
        )
        for fold in folds
    ]
    original_eligible, original_reasons = v50.family_eligibility(
        FAMILY,
        original_results,
    )
    chronological_thresholds = [
        float(value.threshold) for value in original_results
        if value.threshold is not None
    ]
    consensus = consensus_threshold(chronological_thresholds)
    calibration_ends = [
        fold["spec"].base_calibration_end for fold in folds
    ]
    consensus.update({
        "source_fold_names": [fold["spec"].name for fold in folds],
        "source_calibration_end_dates": [
            utc_iso(value) for value in calibration_ends
        ],
        "all_source_calibrations_end_by_train_end": all(
            value <= v43.TRAIN_END for value in calibration_ends
        ),
        "used_validation_returns": False,
        "used_final_audit_quarter": False,
        "used_sealed_results": False,
    })
    fixed_results = [
        evaluate_fold_fixed_threshold(
            dataset,
            macro_by_date,
            cash_history,
            fold,
            float(consensus["consensus_threshold"]),
        )
        for fold in folds
    ]
    fixed_audit = fixed_threshold_audit(fixed_results)
    original_thresholds_exact = tuple(
        chronological_thresholds
    ) == EXPECTED_V50_THRESHOLDS
    original_fold_excess_exact = all(
        abs(value.validation_excess - expected) <= 1e-15
        for value, expected in zip(
            original_results,
            EXPECTED_V50_FOLD_EXCESS,
            strict=True,
        )
    )
    reproduction_ok = bool(
        original_eligible
        and original_thresholds_exact
        and original_fold_excess_exact
        and consensus["all_source_calibrations_end_by_train_end"]
    )
    active_eligible = bool(
        reproduction_ok and fixed_audit["eligible"]
    )
    if active_eligible:
        active_states, crossings, audit_quarter = (
            fit_final_states_and_audit(
                dataset,
                macro_by_date,
                cash_history,
                bundle,
                float(consensus["consensus_threshold"]),
            )
        )
        selected_family = FAMILY
        selected_threshold: float | None = float(
            consensus["consensus_threshold"]
        )
    else:
        active_states, crossings, audit_quarter = disabled_final_states(
            dataset
        )
        selected_family = v50.DISABLED_FAMILY
        selected_threshold = None
    evaluation = v50.evaluate_sealed(
        dataset,
        bundle,
        cash_history,
        active_states,
        crossings,
        selected_family,
        selected_threshold,
    )
    baseline_evaluation = v44_report["evaluation"]
    comparison_v44 = {
        "standard_return_uplift": float(
            evaluation["aggregate_standard_return"]
        ) - float(baseline_evaluation["aggregate_standard_return"]),
        "stress_return_uplift": float(
            evaluation["aggregate_stress_return"]
        ) - float(baseline_evaluation["aggregate_stress_return"]),
        "annualized_return_uplift": float(
            evaluation["annualized_standard_return"]
        ) - float(baseline_evaluation["annualized_standard_return"]),
        "actions_not_increased": int(
            evaluation["target_changing_actions"]
        ) <= int(baseline_evaluation["target_changing_actions"]),
        "maximum_target_exposure_not_increased": float(
            evaluation["maximum_target_exposure"]
        ) <= 0.1000001,
        "never_added_asset": evaluation["never_added_asset"],
        "never_increased_target": evaluation["never_increased_target"],
        "crypto_signal_or_risk_parameters_changed": False,
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_iso(datetime.now(timezone.utc)),
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": True,
        "retrospective": True,
        "untouched_historical_dates": False,
        "universe": list(ASSETS),
        "source": source_report,
        "cash_source": cash_history.source,
        "macro_source": macro_history.source,
        "runtime": v48.runtime_versions(),
        "dataset": v44_report["dataset"],
        "consensus_model": {
            "macro_family": v50.FAMILY,
            "transition_family": FAMILY,
            "transition_window_days": WINDOW_DAYS,
            "active_multiplier": ACTIVE_MULTIPLIER,
            "threshold_grid": list(THRESHOLD_GRID),
            "feature_names": [
                v47.MACRO_FEATURE_NAMES[index]
                for index in v47.FAMILY_COLUMNS[v50.FAMILY]
            ],
            "row_count": len(macro_matrix),
            "date_count": len(macro_by_date),
            "availability_rule": (
                "newest observation dated <= decision date - 1 day"
            ),
        },
        "protocol_sha256": file_sha256(PROTOCOL_PATH),
        "implementation_contract_sha256": file_sha256(CONTRACT_PATH),
        "v50_protocol_sha256": file_sha256(v50.PROTOCOL_PATH),
        "v50_report_sha256": V50_REPORT_SHA256,
        "v44_report_sha256": v44_report["report_sha256"],
        "baseline_report_sha256": baseline_report["report_sha256"],
        "baseline_bundle_sha256": baseline_bundle_sha256,
        "bundle": v43.bundle_summary(bundle),
        "v50_reproduction": {
            "eligible": original_eligible,
            "ineligibility_reasons": original_reasons,
            "thresholds_exact": original_thresholds_exact,
            "fold_excess_exact": original_fold_excess_exact,
            "chronological_thresholds": chronological_thresholds,
            "expected_thresholds": list(EXPECTED_V50_THRESHOLDS),
            "expected_fold_excess": list(EXPECTED_V50_FOLD_EXCESS),
            "minimum_fold_excess": min(
                value.validation_excess for value in original_results
            ),
            "positive_excess_fold_count": sum(
                value.validation_excess > 0.0
                for value in original_results
            ),
            "compounded_excess": v50.compounded_validation_excess(
                original_results
            ),
            "folds": [asdict(value) for value in original_results],
        },
        "consensus_derivation": consensus,
        "fixed_threshold_audit": fixed_audit,
        "selection": {
            "selected_family": selected_family,
            "selected_threshold": selected_threshold,
            "selected_is_disabled_baseline": (
                selected_family == v50.DISABLED_FAMILY
            ),
            "original_v50_eligible": original_eligible,
            "fixed_consensus_eligible": fixed_audit["eligible"],
            "active_eligible": active_eligible,
        },
        "final_audit_quarter": audit_quarter,
        "evaluation": evaluation,
        "comparison_with_v44": comparison_v44,
        "comparison_with_v50": {
            "standard_return_uplift": float(
                evaluation["aggregate_standard_return"]
            ) - V50_FINAL_STANDARD_RETURN,
            "stress_return_uplift": float(
                evaluation["aggregate_stress_return"]
            ) - V50_FINAL_STRESS_RETURN,
            "annualized_return_uplift": float(
                evaluation["annualized_standard_return"]
            ) - V50_FINAL_ANNUALIZED_RETURN,
        },
        "reproduction": {
            **v44_report["reproduction"],
            "walk_forward_fold_count": len(folds),
            "v50_family_fixed": FAMILY,
            "active_multiplier_fixed": ACTIVE_MULTIPLIER,
            "consensus_source_threshold_count": len(
                chronological_thresholds
            ),
            "all_consensus_sources_presealed": bool(
                consensus[
                    "all_source_calibrations_end_by_train_end"
                ]
            ),
            "final_quarter_used_for_selection": False,
            "final_v43_retrained_for_v51": False,
        },
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run v5.1 consensus transition-threshold audit"
    )
    parser.add_argument("--baseline-json", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("evidence/v51/historical.json"),
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
        "selected_family": report["selection"]["selected_family"],
        "selected_threshold": report["selection"]["selected_threshold"],
        "fixed_consensus_eligible": report["selection"][
            "fixed_consensus_eligible"
        ],
        "standard_return": evaluation["aggregate_standard_return"],
        "stress_return": evaluation["aggregate_stress_return"],
        "annualized_standard_return": evaluation[
            "annualized_standard_return"
        ],
        "maximum_drawdown": evaluation["maximum_drawdown"],
        "attenuated_decision_count": evaluation[
            "attenuated_decision_count"
        ],
        "report_sha256": report["report_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
