from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from tradebot.research import distributional_utility_v43 as v43
from tradebot.research import dollar_rates_attenuation_v48 as v48
from tradebot.research import dollar_rates_probability_shock_v49 as v49
from tradebot.research import macro_liquidity_state_v47 as v47
from tradebot.research import walk_forward_selective_veto_v46 as v46
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

SCHEMA_VERSION = "5.0-fresh-macro-transition"
PROTOCOL_PATH = Path("research/V50_FRESH_MACRO_TRANSITION_PROTOCOL.md")
CONTRACT_PATH = Path(
    "research/V501_FRESH_MACRO_TRANSITION_IMPLEMENTATION_CONTRACT.md"
)
FAMILY = "dollar_rates"
TRANSITION_WINDOWS = {
    "fresh_3d": 3,
    "fresh_7d": 7,
    "fresh_14d": 14,
}
TRANSITION_FAMILIES = tuple(TRANSITION_WINDOWS)
STATE_THRESHOLDS = tuple(
    value for value in v47.THRESHOLD_GRID if value is not None
)
ACTIVE_MULTIPLIER = 0.50
DISABLED_FAMILY = "disabled"


class FreshMacroTransitionV50Error(RuntimeError):
    pass


@dataclass(frozen=True)
class TransitionFoldResult:
    fold: str
    transition_family: str
    threshold: float | None
    training_date_count: int
    positive_label_share: float | None
    calibration_months: list[dict[str, Any]]
    calibration_minimum_excess: float
    calibration_compounded_excess: float
    validation_baseline: dict[str, Any]
    validation_transition: dict[str, Any]
    validation_excess: float


def transition_active_by_date(
    probabilities: dict[datetime, float],
    threshold: float,
    window_days: int,
) -> tuple[dict[datetime, bool], dict[datetime, bool]]:
    if not 0.0 < threshold < 1.0:
        raise FreshMacroTransitionV50Error(
            f"state threshold outside (0, 1): {threshold}"
        )
    if window_days <= 0:
        raise FreshMacroTransitionV50Error(
            f"transition window must be positive: {window_days}"
        )
    active: dict[datetime, bool] = {}
    crossings: dict[datetime, bool] = {}
    previous_probability: float | None = None
    episode_start: datetime | None = None
    for stamp in sorted(probabilities):
        probability = float(probabilities[stamp])
        crossing = False
        if previous_probability is None:
            episode_start = None
        elif probability >= threshold:
            episode_start = None
        elif previous_probability >= threshold:
            episode_start = stamp
            crossing = True
        crossings[stamp] = crossing
        active[stamp] = (
            probability < threshold
            and episode_start is not None
            and stamp - episode_start < timedelta(days=window_days)
        )
        previous_probability = probability
    return active, crossings


def simulate_transition(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    cash_history: v44.CashRateHistory,
    active_states: dict[datetime, bool],
    crossings: dict[datetime, bool],
    transition_family: str,
    threshold: float | None,
    *,
    one_way_cost: float,
) -> dict[str, Any]:
    if threshold is None:
        transformed = {stamp: 1.0 for stamp in active_states}
        internal_threshold = None
        multiplier = 1.0
    else:
        transformed = {
            stamp: 0.0 if active else 1.0
            for stamp, active in active_states.items()
        }
        internal_threshold = 0.5
        multiplier = ACTIVE_MULTIPLIER
    summary = v48.simulate_attenuation(
        dataset,
        mask,
        bundle,
        predictions,
        cash_history,
        transformed,
        internal_threshold,
        multiplier,
        one_way_cost=one_way_cost,
    )
    masked_dates = {
        dataset.dates[index] for index in np.flatnonzero(mask)
    }
    return {
        **summary,
        "transition_family": transition_family,
        "state_threshold": threshold,
        "transition_window_days": (
            None if transition_family == DISABLED_FAMILY
            else TRANSITION_WINDOWS[transition_family]
        ),
        "crossing_count": sum(
            bool(crossings.get(stamp, False)) for stamp in masked_dates
        ),
        "active_transition_date_count": sum(
            bool(active_states.get(stamp, False)) for stamp in masked_dates
        ),
    }


def normalized_baseline(summary: dict[str, Any]) -> dict[str, Any]:
    result = v48.normalized_baseline(summary)
    result.setdefault("transition_family", DISABLED_FAMILY)
    result.setdefault("state_threshold", None)
    result.setdefault("transition_window_days", None)
    result.setdefault("crossing_count", 0)
    result.setdefault("active_transition_date_count", 0)
    return result


def calibrate_threshold(
    dataset: Dataset,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    cash_history: v44.CashRateHistory,
    probabilities: dict[datetime, float],
    transition_family: str,
    start: datetime,
    end: datetime,
) -> tuple[float, list[dict[str, Any]], tuple[Any, ...]]:
    window_days = TRANSITION_WINDOWS[transition_family]
    best: tuple[tuple[Any, ...], float, list[dict[str, Any]]] | None = None
    blocks = v48.calendar_month_blocks(start, end)
    for threshold in STATE_THRESHOLDS:
        active_states, crossings = transition_active_by_date(
            probabilities,
            threshold,
            window_days,
        )
        month_results: list[dict[str, Any]] = []
        for name, block_start, block_end in blocks:
            mask = v43.date_mask(dataset, block_start, block_end)
            baseline = v44.simulate(
                dataset,
                mask,
                bundle,
                predictions,
                cash_history,
                one_way_cost=STANDARD_ONE_WAY_COST,
            )
            transition = simulate_transition(
                dataset,
                mask,
                bundle,
                predictions,
                cash_history,
                active_states,
                crossings,
                transition_family,
                threshold,
                one_way_cost=STANDARD_ONE_WAY_COST,
            )
            month_results.append({
                "name": name,
                "start": utc_iso(block_start),
                "end": utc_iso(block_end),
                "baseline": baseline,
                "transition": transition,
                "excess": float(transition["net_return"])
                - float(baseline["net_return"]),
            })
        excess = [float(value["excess"]) for value in month_results]
        key = (
            min(excess),
            v48._compounded_excess(
                [value["transition"] for value in month_results],
                [value["baseline"] for value in month_results],
            ),
            -max(
                float(value["transition"]["maximum_drawdown"])
                for value in month_results
            ),
            -sum(
                float(value["transition"]["turnover"])
                for value in month_results
            ),
            -sum(
                int(value["transition"]["target_changing_actions"])
                for value in month_results
            ),
            -sum(
                int(value["transition"]["attenuated_decision_count"])
                for value in month_results
            ),
            float(threshold),
        )
        if best is None or key > best[0]:
            best = (key, float(threshold), month_results)
    if best is None:
        raise FreshMacroTransitionV50Error(
            "transition threshold calibration produced no candidate"
        )
    return best[1], best[2], best[0]


def fit_and_evaluate_fold(
    dataset: Dataset,
    macro_by_date: dict[datetime, np.ndarray],
    cash_history: v44.CashRateHistory,
    fold: dict[str, Any],
    transition_family: str,
) -> TransitionFoldResult:
    spec = fold["spec"]
    X_train, y_train, training_dates = v47.date_level_samples(
        dataset,
        macro_by_date,
        FAMILY,
        start=None,
        end=spec.training_end,
    )
    macro_model = v47.fit_macro_classifier(X_train, y_train)
    probabilities = v47.probability_by_date(
        macro_model,
        macro_by_date,
        FAMILY,
    )
    threshold, months, key = calibrate_threshold(
        dataset,
        fold["bundle"],
        fold["predictions"],
        cash_history,
        probabilities,
        transition_family,
        spec.base_calibration_start,
        spec.base_calibration_end,
    )
    active_states, crossings = transition_active_by_date(
        probabilities,
        threshold,
        TRANSITION_WINDOWS[transition_family],
    )
    validation = simulate_transition(
        dataset,
        fold["validation_mask"],
        fold["bundle"],
        fold["predictions"],
        cash_history,
        active_states,
        crossings,
        transition_family,
        threshold,
        one_way_cost=STANDARD_ONE_WAY_COST,
    )
    return TransitionFoldResult(
        fold=spec.name,
        transition_family=transition_family,
        threshold=threshold,
        training_date_count=len(training_dates),
        positive_label_share=float(np.mean(y_train)),
        calibration_months=months,
        calibration_minimum_excess=float(key[0]),
        calibration_compounded_excess=float(key[1]),
        validation_baseline=fold["baseline"],
        validation_transition=validation,
        validation_excess=float(validation["net_return"])
        - float(fold["baseline"]["net_return"]),
    )


def compounded_validation_excess(
    results: list[TransitionFoldResult],
) -> float:
    return v48._compounded_excess(
        [value.validation_transition for value in results],
        [value.validation_baseline for value in results],
    )


def family_eligibility(
    transition_family: str,
    results: list[TransitionFoldResult],
) -> tuple[bool, list[str]]:
    if transition_family == DISABLED_FAMILY:
        return True, []
    reasons: list[str] = []
    excess = [value.validation_excess for value in results]
    if compounded_validation_excess(results) <= 0.0:
        reasons.append("non_positive_compounded_excess")
    if sum(value > 0.0 for value in excess) < 4:
        reasons.append("fewer_than_four_positive_excess_folds")
    if min(excess) < -0.0025:
        reasons.append("minimum_fold_excess_below_allowance")
    if sum(
        int(value.validation_transition["target_changing_actions"])
        for value in results
    ) > sum(
        int(value.validation_baseline["target_changing_actions"])
        for value in results
    ):
        reasons.append("increased_actions")
    if sum(
        float(value.validation_transition["turnover"])
        for value in results
    ) > sum(
        float(value.validation_baseline["turnover"])
        for value in results
    ) + 1e-12:
        reasons.append("increased_turnover")
    if any(
        float(value.validation_transition["maximum_drawdown"])
        > float(value.validation_baseline["maximum_drawdown"]) + 0.0025
        for value in results
    ):
        reasons.append("drawdown_allowance_exceeded")
    if sum(
        int(value.validation_transition["attenuated_decision_count"])
        for value in results
    ) == 0:
        reasons.append("no_validation_attenuation")
    return not reasons, reasons


def family_selection_key(
    transition_family: str,
    results: list[TransitionFoldResult],
) -> tuple[Any, ...]:
    excess = [value.validation_excess for value in results]
    return (
        min(excess),
        sum(value > 0.0 for value in excess),
        compounded_validation_excess(results),
        min(
            float(value.validation_transition["net_return"])
            for value in results
        ),
        -max(
            float(value.validation_transition["maximum_drawdown"])
            for value in results
        ),
        -sum(
            float(value.validation_transition["turnover"])
            for value in results
        ),
        -sum(
            int(value.validation_transition["target_changing_actions"])
            for value in results
        ),
        -sum(
            int(value.validation_transition["attenuated_decision_count"])
            for value in results
        ),
        -TRANSITION_WINDOWS[transition_family],
        transition_family,
    )


def select_family(
    family_results: dict[str, list[TransitionFoldResult]],
) -> tuple[str, dict[str, Any]]:
    if not family_results:
        raise FreshMacroTransitionV50Error(
            "transition-family selection requires active candidates"
        )
    exemplar = next(iter(family_results.values()))
    disabled = [
        TransitionFoldResult(
            fold=value.fold,
            transition_family=DISABLED_FAMILY,
            threshold=None,
            training_date_count=value.training_date_count,
            positive_label_share=value.positive_label_share,
            calibration_months=[],
            calibration_minimum_excess=0.0,
            calibration_compounded_excess=0.0,
            validation_baseline=value.validation_baseline,
            validation_transition=normalized_baseline(
                value.validation_baseline
            ),
            validation_excess=0.0,
        )
        for value in exemplar
    ]
    all_results = {DISABLED_FAMILY: disabled, **family_results}
    best_active: tuple[tuple[Any, ...], str] | None = None
    candidates: list[dict[str, Any]] = []
    for transition_family, results in sorted(all_results.items()):
        eligible, reasons = family_eligibility(
            transition_family,
            results,
        )
        key = (
            family_selection_key(transition_family, results)
            if eligible and transition_family != DISABLED_FAMILY
            else None
        )
        candidates.append({
            "transition_family": transition_family,
            "eligible": eligible,
            "ineligibility_reasons": reasons,
            "selection_key": list(key) if key is not None else None,
            "minimum_fold_excess": min(
                value.validation_excess for value in results
            ),
            "positive_excess_fold_count": sum(
                value.validation_excess > 0.0 for value in results
            ),
            "compounded_excess": compounded_validation_excess(results),
            "attenuated_decision_count": sum(
                int(value.validation_transition[
                    "attenuated_decision_count"
                ])
                for value in results
            ),
            "folds": [asdict(value) for value in results],
        })
        if (
            transition_family != DISABLED_FAMILY
            and eligible
            and (best_active is None or key > best_active[0])
        ):
            best_active = (key, transition_family)
    selected = best_active[1] if best_active is not None else DISABLED_FAMILY
    return selected, {
        "selected_family": selected,
        "selected_is_disabled_baseline": selected == DISABLED_FAMILY,
        "selected_key": list(best_active[0]) if best_active is not None else None,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "folds": [asdict(value) for value in all_results[selected]],
    }


def fit_final_rule(
    dataset: Dataset,
    macro_by_date: dict[datetime, np.ndarray],
    cash_history: v44.CashRateHistory,
    bundle: v43.Bundle,
    transition_family: str,
) -> tuple[
    dict[datetime, bool],
    dict[datetime, bool],
    float | None,
    dict[str, Any],
]:
    if transition_family == DISABLED_FAMILY:
        dates = set(dataset.dates)
        return (
            {stamp: False for stamp in dates},
            {stamp: False for stamp in dates},
            None,
            {
                "transition_family": DISABLED_FAMILY,
                "threshold": None,
                "window_days": None,
                "multiplier": 1.0,
                "training_date_count": 0,
                "positive_label_share": None,
                "calibration_months": [],
                "calibration_minimum_excess": 0.0,
                "calibration_compounded_excess": 0.0,
                "attenuated_decision_count": 0,
            },
        )
    X_train, y_train, training_dates = v47.date_level_samples(
        dataset,
        macro_by_date,
        FAMILY,
        start=None,
        end=v43.TRAIN_END,
    )
    macro_model = v47.fit_macro_classifier(X_train, y_train)
    probabilities = v47.probability_by_date(
        macro_model,
        macro_by_date,
        FAMILY,
    )
    predictions = v43.predict_components(bundle, dataset.X)
    threshold, months, key = calibrate_threshold(
        dataset,
        bundle,
        predictions,
        cash_history,
        probabilities,
        transition_family,
        v43.CALIBRATION_START,
        v43.CALIBRATION_END,
    )
    active_states, crossings = transition_active_by_date(
        probabilities,
        threshold,
        TRANSITION_WINDOWS[transition_family],
    )
    return active_states, crossings, threshold, {
        "transition_family": transition_family,
        "threshold": threshold,
        "window_days": TRANSITION_WINDOWS[transition_family],
        "multiplier": ACTIVE_MULTIPLIER,
        "training_date_count": len(training_dates),
        "positive_label_share": float(np.mean(y_train)),
        "calibration_months": months,
        "calibration_minimum_excess": float(key[0]),
        "calibration_compounded_excess": float(key[1]),
        "attenuated_decision_count": sum(
            int(value["transition"]["attenuated_decision_count"])
            for value in months
        ),
    }


def evaluate_sealed(
    dataset: Dataset,
    bundle: v43.Bundle,
    cash_history: v44.CashRateHistory,
    active_states: dict[datetime, bool],
    crossings: dict[datetime, bool],
    transition_family: str,
    threshold: float | None,
) -> dict[str, Any]:
    if threshold is None:
        transformed = {stamp: 1.0 for stamp in active_states}
        internal_threshold = None
        multiplier = 1.0
    else:
        transformed = {
            stamp: 0.0 if active else 1.0
            for stamp, active in active_states.items()
        }
        internal_threshold = 0.5
        multiplier = ACTIVE_MULTIPLIER
    evaluation = v48.evaluate_sealed(
        dataset,
        bundle,
        cash_history,
        transformed,
        internal_threshold,
        multiplier,
    )
    sealed_dates: set[datetime] = set()
    for _, start, end in v43.SEALED_WINDOWS:
        sealed_dates.update(
            dataset.dates[index]
            for index in np.flatnonzero(v43.date_mask(dataset, start, end))
        )
    return {
        **evaluation,
        "transition_family": transition_family,
        "state_threshold": threshold,
        "transition_window_days": (
            None if transition_family == DISABLED_FAMILY
            else TRANSITION_WINDOWS[transition_family]
        ),
        "active_multiplier": multiplier,
        "crossing_count": sum(
            bool(crossings.get(stamp, False)) for stamp in sealed_dates
        ),
        "active_transition_date_count": sum(
            bool(active_states.get(stamp, False)) for stamp in sealed_dates
        ),
    }


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
        raise FreshMacroTransitionV50Error(
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
    family_results = {
        transition_family: [
            fit_and_evaluate_fold(
                dataset,
                macro_by_date,
                cash_history,
                fold,
                transition_family,
            )
            for fold in folds
        ]
        for transition_family in TRANSITION_FAMILIES
    }
    selected_family, selection = select_family(family_results)
    active_states, crossings, threshold, final_calibration = fit_final_rule(
        dataset,
        macro_by_date,
        cash_history,
        bundle,
        selected_family,
    )
    evaluation = evaluate_sealed(
        dataset,
        bundle,
        cash_history,
        active_states,
        crossings,
        selected_family,
        threshold,
    )
    baseline_evaluation = v44_report["evaluation"]
    comparison = {
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
        "transition_model": {
            "macro_family": FAMILY,
            "feature_names": [
                v47.MACRO_FEATURE_NAMES[index]
                for index in v47.FAMILY_COLUMNS[FAMILY]
            ],
            "transition_families": list(TRANSITION_FAMILIES),
            "transition_windows": TRANSITION_WINDOWS,
            "thresholds": list(STATE_THRESHOLDS),
            "active_multiplier": ACTIVE_MULTIPLIER,
            "row_count": len(macro_matrix),
            "date_count": len(macro_by_date),
            "availability_rule": (
                "newest observation dated <= decision date - 1 day"
            ),
        },
        "protocol_sha256": file_sha256(PROTOCOL_PATH),
        "implementation_contract_sha256": file_sha256(CONTRACT_PATH),
        "v49_protocol_sha256": file_sha256(v49.PROTOCOL_PATH),
        "v49_report_sha256": (
            "c6c8eecf73cb6b49a5e43a9e00fca631"
            "cf3e218fd10b060ec491683d4dc10ee4"
        ),
        "v48_protocol_sha256": file_sha256(v48.PROTOCOL_PATH),
        "v47_protocol_sha256": file_sha256(v47.PROTOCOL_PATH),
        "v44_report_sha256": v44_report["report_sha256"],
        "baseline_report_sha256": baseline_report["report_sha256"],
        "baseline_bundle_sha256": baseline_bundle_sha256,
        "bundle": v43.bundle_summary(bundle),
        "selection": selection,
        "final_calibration": final_calibration,
        "evaluation": evaluation,
        "comparison_with_v44": comparison,
        "reproduction": {
            **v44_report["reproduction"],
            "walk_forward_fold_count": len(folds),
            "active_transition_family_count": len(
                TRANSITION_FAMILIES
            ),
            "active_multiplier_fixed": ACTIVE_MULTIPLIER,
            "final_v43_retrained_for_v50": False,
        },
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run v5.0 fresh macro-transition attenuation"
    )
    parser.add_argument("--baseline-json", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("evidence/v50/historical.json"),
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
        "selected_threshold": report["final_calibration"]["threshold"],
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
