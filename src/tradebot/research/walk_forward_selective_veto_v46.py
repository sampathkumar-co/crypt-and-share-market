from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from tradebot.research import distributional_utility_v43 as v43
from tradebot.research import regime_diversified_utility_v45 as v45
from tradebot.research import yield_bearing_cash_v44 as v44
from tradebot.research import yield_bearing_cash_v44_reproduce as v44_reproduce
from tradebot.research.regime_ranking_v42 import (
    ASSETS,
    REGIME_NAMES,
    STANDARD_ONE_WAY_COST,
    STRESS_ONE_WAY_COST,
    Dataset,
    build_dataset,
    file_sha256,
    model_grid,
    positive_share,
)
from tradebot.research.regime_ranking_v42_sources import (
    canonical_json,
    load_all_sources,
    utc_iso,
)

SCHEMA_VERSION = "4.6-walk-forward-selective-veto"
PROTOCOL_PATH = Path("research/V46_WALK_FORWARD_SELECTIVE_VETO_PROTOCOL.md")
CONTRACT_PATH = Path(
    "research/V461_WALK_FORWARD_SELECTIVE_VETO_IMPLEMENTATION_CONTRACT.md"
)


class WalkForwardSelectiveVetoV46Error(RuntimeError):
    pass


@dataclass(frozen=True)
class FoldSpec:
    name: str
    training_end: datetime
    base_calibration_start: datetime
    base_calibration_end: datetime
    validation_start: datetime
    validation_end: datetime


@dataclass(frozen=True)
class VetoConfig:
    q20_floor: float | None
    dispersion_quantile: float | None
    veto_worst_q20: bool
    minimum_utility_margin: float


WALK_FORWARD_FOLDS = (
    FoldSpec(
        "WF-1",
        v43.day("2023-12-31"),
        v43.day("2024-01-01"),
        v43.day("2024-03-31"),
        v43.day("2024-04-01"),
        v43.day("2024-06-30"),
    ),
    FoldSpec(
        "WF-2",
        v43.day("2024-03-31"),
        v43.day("2024-04-01"),
        v43.day("2024-06-30"),
        v43.day("2024-07-01"),
        v43.day("2024-09-30"),
    ),
    FoldSpec(
        "WF-3",
        v43.day("2024-06-30"),
        v43.day("2024-07-01"),
        v43.day("2024-09-30"),
        v43.day("2024-10-01"),
        v43.day("2024-12-31"),
    ),
    FoldSpec(
        "WF-4",
        v43.day("2024-09-30"),
        v43.day("2024-10-01"),
        v43.day("2024-12-31"),
        v43.day("2025-01-01"),
        v43.day("2025-03-31"),
    ),
    FoldSpec(
        "WF-5",
        v43.day("2024-12-31"),
        v43.day("2025-01-01"),
        v43.day("2025-03-31"),
        v43.day("2025-04-01"),
        v43.day("2025-06-30"),
    ),
    FoldSpec(
        "WF-6",
        v43.day("2025-03-31"),
        v43.day("2025-04-01"),
        v43.day("2025-06-30"),
        v43.day("2025-07-01"),
        v43.day("2025-09-30"),
    ),
)

DISABLED_VETO = VetoConfig(None, None, False, 0.0)


def date_mask(
    dataset: Dataset,
    start: datetime | None,
    end: datetime,
) -> np.ndarray:
    return v43.date_mask(dataset, start, end)


def recency_masks_at(
    dataset: Dataset,
    training_end: datetime,
) -> dict[str, np.ndarray]:
    starts = {
        "full": None,
        "days720": training_end - timedelta(days=719),
        "days360": training_end - timedelta(days=359),
    }
    base = date_mask(dataset, None, training_end)
    return {
        name: base & date_mask(dataset, start, training_end)
        for name, start in starts.items()
    }


def fit_family_at(
    dataset: Dataset,
    config: dict[str, Any],
    training_end: datetime,
) -> v43.Bundle:
    masks = recency_masks_at(dataset, training_end)
    specialists: dict[int, v43.Specialist] = {}
    for regime in v43.LONG_REGIMES:
        members: list[v43.Member] = []
        for name, mask in masks.items():
            specialist_mask = mask & (dataset.regimes == regime)
            if int(np.sum(specialist_mask)) < 250:
                continue
            members.append(v43._fit_member(
                dataset,
                specialist_mask,
                config,
                name,
            ))
        if len(members) >= 2:
            specialists[regime] = v43.Specialist(members)

    regime_models: list[Any] = []
    regime_window_names: list[str] = []
    for index, (name, mask) in enumerate(masks.items()):
        if int(np.sum(mask)) < 500:
            continue
        regime_models.append(v43._fit_classifier(
            dataset.X[mask],
            dataset.regimes[mask],
            config,
            101 + index,
        ))
        regime_window_names.append(name)
    if len(regime_models) < 2:
        raise WalkForwardSelectiveVetoV46Error(
            f"{training_end.date()} produced fewer than two regime models"
        )
    if not specialists:
        raise WalkForwardSelectiveVetoV46Error(
            f"{training_end.date()} produced no specialists"
        )
    return v43.Bundle(
        specialists=specialists,
        regime_models=regime_models,
        regime_window_names=regime_window_names,
        config=dict(config),
        panic_threshold=0.45,
        utility_threshold=0.004,
        q20_floor=-0.03,
        top_n=1,
        disagreement_quantile=0.75,
        disagreement_threshold=float("inf"),
        feature_names=dataset.feature_names,
    )


def train_base_bundle(
    dataset: Dataset,
    fold: FoldSpec,
) -> tuple[v43.Bundle, dict[str, Any]]:
    training = date_mask(dataset, None, fold.training_end)
    calibration = date_mask(
        dataset,
        fold.base_calibration_start,
        fold.base_calibration_end,
    )
    if int(np.sum(training)) < 1000:
        raise WalkForwardSelectiveVetoV46Error(
            f"{fold.name} has insufficient training rows"
        )
    if int(np.sum(calibration)) < 250:
        raise WalkForwardSelectiveVetoV46Error(
            f"{fold.name} has insufficient base-calibration rows"
        )

    best: tuple[
        tuple[float, ...], v43.Bundle, dict[str, Any]
    ] | None = None
    for config in model_grid():
        base = fit_family_at(dataset, config, fold.training_end)
        predictions = v43.predict_components(base, dataset.X)
        for panic_threshold in (0.45, 0.55, 0.65):
            panic_bundle = v43.configured_bundle(
                base,
                panic_threshold=panic_threshold,
                utility_threshold=0.004,
                q20_floor=-0.03,
                top_n=1,
                disagreement_quantile=0.75,
                disagreement_threshold=float("inf"),
            )
            disagreements = v43.calibration_disagreements(
                dataset,
                calibration,
                panic_bundle,
                predictions,
            )
            if not len(disagreements):
                continue
            for quantile in (0.75, 0.90):
                threshold = float(np.quantile(disagreements, quantile))
                for utility_threshold in (0.004, 0.008, 0.012):
                    for q20_floor in (-0.03, -0.02, -0.01):
                        for top_n in (1, 2):
                            bundle = v43.configured_bundle(
                                base,
                                panic_threshold=panic_threshold,
                                utility_threshold=utility_threshold,
                                q20_floor=q20_floor,
                                top_n=top_n,
                                disagreement_quantile=quantile,
                                disagreement_threshold=threshold,
                            )
                            summary = v43.simulate(
                                dataset,
                                calibration,
                                bundle,
                                predictions,
                                one_way_cost=STANDARD_ONE_WAY_COST,
                            )
                            score = (
                                float(summary["net_return"])
                                - 2.0 * float(summary["maximum_drawdown"])
                                - 0.25 * float(summary["turnover"])
                            )
                            if summary["target_changing_actions"] < 8:
                                score -= 1.0
                            key = v43.calibration_tie_key(
                                score,
                                summary,
                                bundle,
                            )
                            if best is None or key > best[0]:
                                best = (key, bundle, summary)
    if best is None:
        raise WalkForwardSelectiveVetoV46Error(
            f"{fold.name} base calibration produced no bundle"
        )
    selected = best[1]
    return selected, {
        "fold": fold.name,
        "training_end": utc_iso(fold.training_end),
        "base_calibration_start": utc_iso(fold.base_calibration_start),
        "base_calibration_end": utc_iso(fold.base_calibration_end),
        "validation_start": utc_iso(fold.validation_start),
        "validation_end": utc_iso(fold.validation_end),
        "selection_key": list(best[0]),
        "calibration_summary": best[2],
        "bundle": v43.bundle_summary(selected),
    }


def dispersion_thresholds(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
) -> dict[float, float]:
    values = v45.calibration_dispersion_values(
        dataset,
        mask,
        bundle,
        predictions,
        panic_threshold=bundle.panic_threshold,
    )
    if not len(values):
        raise WalkForwardSelectiveVetoV46Error(
            "no cross-regime dispersion observations"
        )
    return {
        0.75: float(np.quantile(values, 0.75)),
        0.90: float(np.quantile(values, 0.90)),
    }


def veto_grid() -> list[VetoConfig]:
    values = [
        VetoConfig(q20, dispersion, worst, margin)
        for q20, dispersion, worst, margin in itertools.product(
            (None, -0.03, -0.02, -0.01),
            (None, 0.75, 0.90),
            (False, True),
            (0.0, 0.002, 0.004),
        )
    ]
    return sorted(
        set(values),
        key=lambda value: (
            value != DISABLED_VETO,
            value.q20_floor is not None,
            value.q20_floor if value.q20_floor is not None else -1.0,
            value.dispersion_quantile is not None,
            value.dispersion_quantile or 0.0,
            value.veto_worst_q20,
            value.minimum_utility_margin,
        ),
    )


def selective_decisions_by_date(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    config: VetoConfig,
    *,
    calibrated_dispersion_thresholds: dict[float, float],
) -> dict[datetime, dict[str, Any]]:
    baseline = v43.decisions_by_date(
        dataset,
        mask,
        bundle,
        predictions,
    )
    if config == DISABLED_VETO:
        return {
            stamp: {
                **decision,
                "panic": decision["regime"] == 2,
                "selected_regimes": {
                    dataset.assets[index]: int(decision["regime"])
                    for index in decision["selected"]
                },
                "vetoed_assets": [],
                "veto_reasons": {},
            }
            for stamp, decision in baseline.items()
        }

    contexts = v43.date_contexts(dataset, mask, bundle, predictions)
    result: dict[datetime, dict[str, Any]] = {}
    for stamp, decision in baseline.items():
        context = contexts[stamp]
        regime = decision["regime"]
        selected = list(decision["selected"])
        vetoed: list[str] = []
        veto_reasons: dict[str, list[str]] = {}
        q20_by_index: dict[int, float] = {}
        if regime is not None and regime != 2:
            specialist = predictions["specialists"][regime]
            probability_std = float(context["std_probabilities"][regime])
            for index in context["indexes"]:
                q20_by_index[index] = float(v43.candidate_metrics(
                    specialist,
                    index,
                    probability_std,
                )["q20"])
        worst_index: int | None = None
        if q20_by_index:
            worst_index = sorted(
                q20_by_index,
                key=lambda index: (q20_by_index[index], dataset.assets[index]),
            )[0]

        kept: list[int] = []
        for index in selected:
            reasons: list[str] = []
            if regime is None or regime == 2:
                kept.append(index)
                continue
            specialist = predictions["specialists"][regime]
            metrics = v43.candidate_metrics(
                specialist,
                index,
                float(context["std_probabilities"][regime]),
            )
            mixed = v45.mixed_candidate_metrics(
                bundle,
                predictions,
                context,
                index,
                entropy_penalty=0.0,
                dispersion_penalty=0.0,
            )
            if (
                config.q20_floor is not None
                and float(metrics["q20"]) < config.q20_floor
            ):
                reasons.append("q20_floor")
            if config.dispersion_quantile is not None:
                threshold = calibrated_dispersion_thresholds[
                    config.dispersion_quantile
                ]
                if float(mixed["cross_regime_dispersion"]) > threshold:
                    reasons.append("cross_regime_dispersion")
            if config.veto_worst_q20 and index == worst_index:
                reasons.append("worst_cross_sectional_q20")
            utility_margin = (
                float(metrics["utility"]) - bundle.utility_threshold
            )
            if utility_margin < config.minimum_utility_margin:
                reasons.append("utility_margin")
            asset = dataset.assets[index]
            if reasons:
                vetoed.append(asset)
                veto_reasons[asset] = reasons
            else:
                kept.append(index)
        result[stamp] = {
            **decision,
            "panic": regime == 2,
            "selected": kept,
            "selected_regimes": {
                dataset.assets[index]: int(regime) for index in kept
            },
            "vetoed_assets": sorted(vetoed),
            "veto_reasons": veto_reasons,
        }
    return result


def simulate_veto(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    history: v44.CashRateHistory,
    config: VetoConfig,
    *,
    calibrated_dispersion_thresholds: dict[float, float],
    one_way_cost: float,
) -> dict[str, Any]:
    decisions = selective_decisions_by_date(
        dataset,
        mask,
        bundle,
        predictions,
        config,
        calibrated_dispersion_thresholds=calibrated_dispersion_thresholds,
    )
    index_map = {
        (dataset.dates[index], dataset.assets[index]): index
        for index in np.flatnonzero(mask)
    }
    cash = 1.0
    holdings = {asset: 0.0 for asset in ASSETS}
    holding_regime = {asset: 0 for asset in ASSETS}
    selected_assets: tuple[str, ...] = ()
    selected_regimes: dict[str, int] = {}
    selected_ever: set[str] = set()
    vetoed_ever: set[str] = set()
    veto_reason_counts: dict[str, int] = {}
    peak = 1.0
    maximum_drawdown = 0.0
    turnover = 0.0
    action_count = 0
    age = 3
    maximum_gross_exposure = 0.0
    maximum_target_exposure = 0.0
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    regime_contribution = {
        name: 0.0 for name in REGIME_NAMES.values()
    }
    cash_contribution = 0.0

    for stamp in sorted(decisions):
        equity_before = cash + sum(holdings.values())
        decision = decisions[stamp]
        vetoed_ever.update(decision["vetoed_assets"])
        for reasons in decision["veto_reasons"].values():
            for reason in reasons:
                veto_reason_counts[reason] = veto_reason_counts.get(reason, 0) + 1
        panic = bool(decision["panic"])
        due = age >= 3
        target_assets = selected_assets
        target_regimes = selected_regimes
        if panic:
            target_assets = ()
            target_regimes = {}
        elif due:
            target_assets = tuple(
                dataset.assets[index] for index in decision["selected"]
            )
            target_regimes = dict(decision["selected_regimes"])

        if panic or due:
            target_values = {
                asset: (
                    0.05 * equity_before
                    if asset in target_assets
                    else 0.0
                )
                for asset in ASSETS
            }
            maximum_target_exposure = max(
                maximum_target_exposure,
                sum(target_values.values()) / max(equity_before, 1e-12),
            )
            traded = sum(
                abs(target_values[asset] - holdings[asset])
                for asset in ASSETS
            )
            changed = traded > 1e-12
            if changed:
                cash -= one_way_cost * traded
                turnover += traded
                action_count += 1
            cash += sum(
                holdings[asset] - target_values[asset] for asset in ASSETS
            )
            holdings = target_values
            selected_assets = target_assets
            selected_regimes = target_regimes
            selected_ever.update(target_assets)
            for asset in target_assets:
                holding_regime[asset] = int(target_regimes[asset])
            if due or (panic and changed):
                age = 0

        equity_open = cash + sum(holdings.values())
        maximum_gross_exposure = max(
            maximum_gross_exposure,
            sum(holdings.values()) / max(equity_open, 1e-12),
        )
        _, annual_rate = v44.prior_known_annual_rate(history, stamp)
        cash_yield = cash * v44.annual_to_daily_rate(annual_rate)
        cash += cash_yield
        cash_contribution += cash_yield
        for asset in ASSETS:
            if holdings[asset] <= 0.0:
                continue
            index = index_map[(stamp, asset)]
            asset_return = float(dataset.return1[index])
            contribution = holdings[asset] * asset_return
            holdings[asset] *= 1.0 + asset_return
            asset_contribution[asset] += contribution
            regime_contribution[REGIME_NAMES[holding_regime[asset]]] += contribution
        equity_close = cash + sum(holdings.values())
        peak = max(peak, equity_close)
        maximum_drawdown = max(
            maximum_drawdown,
            1.0 - equity_close / peak,
        )
        age += 1

    liquidation = sum(holdings.values())
    if liquidation > 0.0:
        cash += liquidation - one_way_cost * liquidation
        turnover += liquidation
        maximum_drawdown = max(
            maximum_drawdown,
            1.0 - cash / max(peak, 1e-12),
        )
    return {
        "net_return": cash - 1.0,
        "maximum_drawdown": maximum_drawdown,
        "turnover": turnover,
        "target_changing_actions": action_count,
        "selected_assets": sorted(selected_ever),
        "vetoed_assets": sorted(vetoed_ever),
        "veto_reason_counts": veto_reason_counts,
        "asset_contribution": asset_contribution,
        "regime_contribution": regime_contribution,
        "cash_contribution": cash_contribution,
        "decision_count": len(decisions),
        "maximum_gross_exposure": maximum_gross_exposure,
        "maximum_target_exposure": maximum_target_exposure,
    }


def config_eligibility(
    fold_results: list[dict[str, Any]],
    config: VetoConfig,
) -> tuple[bool, list[str]]:
    if config == DISABLED_VETO:
        return True, []
    reasons: list[str] = []
    excess = [
        float(value["veto"]["net_return"])
        - float(value["baseline"]["net_return"])
        for value in fold_results
    ]
    if min(excess) < -1e-12:
        reasons.append("negative_minimum_fold_excess")
    baseline_actions = sum(
        int(value["baseline"]["target_changing_actions"])
        for value in fold_results
    )
    veto_actions = sum(
        int(value["veto"]["target_changing_actions"])
        for value in fold_results
    )
    if veto_actions > baseline_actions:
        reasons.append("increased_actions")
    baseline_turnover = sum(
        float(value["baseline"]["turnover"]) for value in fold_results
    )
    veto_turnover = sum(
        float(value["veto"]["turnover"]) for value in fold_results
    )
    if veto_turnover > baseline_turnover + 1e-12:
        reasons.append("increased_turnover")
    if any(
        float(value["veto"]["maximum_drawdown"])
        > float(value["baseline"]["maximum_drawdown"]) + 0.0025
        for value in fold_results
    ):
        reasons.append("drawdown_allowance_exceeded")
    return not reasons, reasons


def selection_key(
    fold_results: list[dict[str, Any]],
    config: VetoConfig,
) -> tuple[float, ...]:
    excess = [
        float(value["veto"]["net_return"])
        - float(value["baseline"]["net_return"])
        for value in fold_results
    ]
    returns = [float(value["veto"]["net_return"]) for value in fold_results]
    compounded_excess = (
        np.prod([1.0 + value for value in excess]) - 1.0
    )
    intervention_count = sum(
        sum(value["veto"]["veto_reason_counts"].values())
        for value in fold_results
    )
    return (
        min(excess),
        float(sum(value > 0.0 for value in excess)),
        float(compounded_excess),
        min(returns),
        -max(float(value["veto"]["maximum_drawdown"]) for value in fold_results),
        -sum(float(value["veto"]["turnover"]) for value in fold_results),
        -float(sum(
            int(value["veto"]["target_changing_actions"])
            for value in fold_results
        )),
        -float(intervention_count),
        -float(config.q20_floor is not None),
        -(config.q20_floor or -1.0),
        -float(config.dispersion_quantile is not None),
        -(config.dispersion_quantile or 0.0),
        -float(config.veto_worst_q20),
        -config.minimum_utility_margin,
    )


def build_walk_forward_folds(
    dataset: Dataset,
    history: v44.CashRateHistory,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for fold in WALK_FORWARD_FOLDS:
        bundle, training_report = train_base_bundle(dataset, fold)
        predictions = v43.predict_components(bundle, dataset.X)
        calibration_mask = date_mask(
            dataset,
            fold.base_calibration_start,
            fold.base_calibration_end,
        )
        thresholds = dispersion_thresholds(
            dataset,
            calibration_mask,
            bundle,
            predictions,
        )
        validation_mask = date_mask(
            dataset,
            fold.validation_start,
            fold.validation_end,
        )
        baseline = v44.simulate(
            dataset,
            validation_mask,
            bundle,
            predictions,
            history,
            one_way_cost=STANDARD_ONE_WAY_COST,
        )
        result.append({
            "spec": fold,
            "bundle": bundle,
            "predictions": predictions,
            "validation_mask": validation_mask,
            "dispersion_thresholds": thresholds,
            "training_report": training_report,
            "baseline": baseline,
        })
    return result


def select_veto(
    dataset: Dataset,
    history: v44.CashRateHistory,
    folds: list[dict[str, Any]],
) -> tuple[VetoConfig, dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    best: tuple[tuple[float, ...], VetoConfig, list[dict[str, Any]]] | None = None
    for config in veto_grid():
        fold_results: list[dict[str, Any]] = []
        for fold in folds:
            veto = simulate_veto(
                dataset,
                fold["validation_mask"],
                fold["bundle"],
                fold["predictions"],
                history,
                config,
                calibrated_dispersion_thresholds=(
                    fold["dispersion_thresholds"]
                ),
                one_way_cost=STANDARD_ONE_WAY_COST,
            )
            fold_results.append({
                "name": fold["spec"].name,
                "baseline": fold["baseline"],
                "veto": veto,
            })
        eligible, reasons = config_eligibility(fold_results, config)
        key = selection_key(fold_results, config) if eligible else None
        candidates.append({
            "config": asdict(config),
            "eligible": eligible,
            "ineligibility_reasons": reasons,
            "selection_key": list(key) if key is not None else None,
            "minimum_fold_excess": min(
                float(value["veto"]["net_return"])
                - float(value["baseline"]["net_return"])
                for value in fold_results
            ),
            "positive_excess_fold_count": sum(
                float(value["veto"]["net_return"])
                > float(value["baseline"]["net_return"])
                for value in fold_results
            ),
        })
        if eligible and (best is None or key > best[0]):
            best = (key, config, fold_results)
    if best is None:
        raise WalkForwardSelectiveVetoV46Error(
            "veto selection unexpectedly produced no eligible baseline"
        )
    selected_fold_reports = []
    for fold, values in zip(folds, best[2], strict=True):
        selected_fold_reports.append({
            "name": fold["spec"].name,
            "training": fold["training_report"],
            "dispersion_thresholds": {
                str(key): value
                for key, value in fold["dispersion_thresholds"].items()
            },
            "baseline": values["baseline"],
            "veto": values["veto"],
            "excess_return": (
                float(values["veto"]["net_return"])
                - float(values["baseline"]["net_return"])
            ),
        })
    return best[1], {
        "selected_config": asdict(best[1]),
        "selected_key": list(best[0]),
        "selected_is_disabled_baseline": best[1] == DISABLED_VETO,
        "folds": selected_fold_reports,
        "candidate_count": len(candidates),
        "eligible_candidate_count": sum(
            value["eligible"] for value in candidates
        ),
        "candidates": candidates,
    }


def evaluate_final(
    dataset: Dataset,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    history: v44.CashRateHistory,
    config: VetoConfig,
    thresholds: dict[float, float],
    *,
    v44_baseline: dict[str, Any],
) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    for name, start, end in v43.SEALED_WINDOWS:
        mask = date_mask(dataset, start, end)
        standard = simulate_veto(
            dataset,
            mask,
            bundle,
            predictions,
            history,
            config,
            calibrated_dispersion_thresholds=thresholds,
            one_way_cost=STANDARD_ONE_WAY_COST,
        )
        stress = simulate_veto(
            dataset,
            mask,
            bundle,
            predictions,
            history,
            config,
            calibrated_dispersion_thresholds=thresholds,
            one_way_cost=STRESS_ONE_WAY_COST,
        )
        days = len({
            dataset.dates[index] for index in np.flatnonzero(mask)
        })
        standard["verification_days"] = days
        stress["verification_days"] = days
        windows.append({
            "name": name,
            "start": utc_iso(start),
            "end": utc_iso(end),
            "standard": standard,
            "stress": stress,
        })
    standard_returns = [
        float(value["standard"]["net_return"]) for value in windows
    ]
    stress_returns = [
        float(value["stress"]["net_return"]) for value in windows
    ]
    aggregate_standard = v45._compound(standard_returns)
    aggregate_stress = v45._compound(stress_returns)
    verification_days = sum(
        int(value["standard"]["verification_days"]) for value in windows
    )
    annualized = (
        (1.0 + aggregate_standard) ** (365.0 / verification_days) - 1.0
        if verification_days > 0 and aggregate_standard > -1.0
        else -1.0
    )
    maximum_drawdown = max(
        max(
            float(value["standard"]["maximum_drawdown"]),
            float(value["stress"]["maximum_drawdown"]),
        )
        for value in windows
    )
    actions = sum(
        int(value["standard"]["target_changing_actions"])
        for value in windows
    )
    selected_assets = sorted(set().union(*[
        set(value["standard"]["selected_assets"]) for value in windows
    ]))
    vetoed_assets = sorted(set().union(*[
        set(value["standard"]["vetoed_assets"]) for value in windows
    ]))
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    regime_contribution = {
        name: 0.0 for name in REGIME_NAMES.values()
    }
    cash_contribution = 0.0
    for value in windows:
        cash_contribution += float(value["standard"]["cash_contribution"])
        for asset, contribution in value["standard"][
            "asset_contribution"
        ].items():
            asset_contribution[asset] += float(contribution)
        for regime, contribution in value["standard"][
            "regime_contribution"
        ].items():
            regime_contribution[regime] += float(contribution)
    asset_share = positive_share(list(asset_contribution.values()))
    window_share = positive_share(standard_returns)
    regime_share = positive_share(list(regime_contribution.values()))
    gates = {
        "five_positive_standard_windows": all(
            value > 0.0 for value in standard_returns
        ),
        "four_positive_stress_windows": sum(
            value > 0.0 for value in stress_returns
        ) >= 4,
        "annualized_standard_at_least_five_percent": annualized >= 0.05,
        "aggregate_stress_positive": aggregate_stress > 0.0,
        "drawdown_cap": maximum_drawdown <= 0.10,
        "twenty_costed_actions": actions >= 20,
        "asset_diversity": (
            "BTC" in selected_assets
            and len(set(selected_assets) - {"BTC"}) >= 2
        ),
        "asset_concentration": asset_share <= 0.70,
        "window_concentration": window_share <= 0.70,
        "regime_concentration": regime_share <= 0.70,
        "independent_source_replication": False,
        "current_market_smoke": False,
        "untouched_historical_dates": False,
    }
    historical_only = all(
        value
        for key, value in gates.items()
        if key not in {
            "independent_source_replication",
            "current_market_smoke",
            "untouched_historical_dates",
        }
    )
    return {
        "windows": windows,
        "aggregate_standard_return": aggregate_standard,
        "aggregate_stress_return": aggregate_stress,
        "annualized_standard_return": annualized,
        "maximum_drawdown": maximum_drawdown,
        "verification_days": verification_days,
        "target_changing_actions": actions,
        "selected_assets": selected_assets,
        "vetoed_assets": vetoed_assets,
        "asset_contribution": asset_contribution,
        "regime_contribution": regime_contribution,
        "cash_contribution": cash_contribution,
        "maximum_positive_asset_share": asset_share,
        "maximum_positive_window_share": window_share,
        "maximum_positive_regime_share": regime_share,
        "standard_window_returns": standard_returns,
        "stress_window_returns": stress_returns,
        "gates": gates,
        "v44_comparison": {
            "standard_return_change": aggregate_standard - float(
                v44_baseline["aggregate_standard_return"]
            ),
            "stress_return_change": aggregate_stress - float(
                v44_baseline["aggregate_stress_return"]
            ),
            "annualized_return_change": annualized - float(
                v44_baseline["annualized_standard_return"]
            ),
            "action_count_change": actions - int(
                v44_baseline["target_changing_actions"]
            ),
            "selected_assets_changed": (
                selected_assets != list(v44_baseline["selected_assets"])
            ),
        },
        "status": (
            "RETROSPECTIVE_HISTORICAL_BREAKTHROUGH_PENDING_REPLICATION_AND_SMOKE"
            if historical_only
            else "RETROSPECTIVE_NOT_YET_BREAKTHROUGH"
        ),
    }


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
    v44_reproduce.validate_baseline_report(baseline_report)
    if states is None:
        states, source_report = load_all_sources(
            monthly_workers=monthly_workers,
            metrics_workers=metrics_workers,
        )
    if source_report is None:
        raise WalkForwardSelectiveVetoV46Error(
            "current source report is unavailable"
        )
    if canonical_json(source_report) != canonical_json(
        baseline_report["source"]
    ):
        raise WalkForwardSelectiveVetoV46Error(
            "current source inventory differs from frozen v4.3"
        )
    dataset = build_dataset(states)
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
    if canonical_json(observed_dataset) != canonical_json(
        baseline_report["dataset"]
    ):
        raise WalkForwardSelectiveVetoV46Error(
            "current dataset metadata differs from frozen v4.3"
        )
    if canonical_json(v43.bundle_summary(final_bundle)) != canonical_json(
        baseline_report["bundle"]
    ):
        raise WalkForwardSelectiveVetoV46Error(
            "final bundle differs from frozen v4.3 report"
        )
    reproduced_v43 = v43.evaluate_sealed(dataset, final_bundle)
    if canonical_json(reproduced_v43) != canonical_json(
        baseline_report["evaluation"]
    ):
        raise WalkForwardSelectiveVetoV46Error(
            "final bundle does not reproduce frozen v4.3"
        )
    if cash_history is None:
        cash_history = v44.load_cash_history()
    if min(cash_history.annual_rates) > min(dataset.dates):
        raise WalkForwardSelectiveVetoV46Error(
            "cash history starts after dataset"
        )

    folds = build_walk_forward_folds(dataset, cash_history)
    selected_config, selection = select_veto(
        dataset,
        cash_history,
        folds,
    )
    final_predictions = v43.predict_components(final_bundle, dataset.X)
    final_calibration_mask = date_mask(
        dataset,
        v43.CALIBRATION_START,
        v43.CALIBRATION_END,
    )
    final_thresholds = dispersion_thresholds(
        dataset,
        final_calibration_mask,
        final_bundle,
        final_predictions,
    )
    v44_baseline = v44.evaluate_sealed(
        dataset,
        final_bundle,
        cash_history,
        baseline=reproduced_v43,
    )
    evaluation = evaluate_final(
        dataset,
        final_bundle,
        final_predictions,
        cash_history,
        selected_config,
        final_thresholds,
        v44_baseline=v44_baseline,
    )
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
        "runtime": v44.runtime_versions(),
        "dataset": observed_dataset,
        "protocol_sha256": file_sha256(PROTOCOL_PATH),
        "implementation_contract_sha256": file_sha256(CONTRACT_PATH),
        "implementation_sha256": file_sha256(Path(__file__).resolve()),
        "baseline_report_sha256": baseline_report["report_sha256"],
        "baseline_bundle_sha256": baseline_bundle_sha256,
        "final_bundle": v43.bundle_summary(final_bundle),
        "reproduction": {
            "source_inventory_exact": True,
            "dataset_metadata_exact": True,
            "bundle_summary_exact": True,
            "v43_evaluation_exact": True,
            "final_v43_retrained_for_v46": False,
            "walk_forward_fold_count": len(folds),
        },
        "selection": selection,
        "final_dispersion_thresholds": {
            str(key): value for key, value in final_thresholds.items()
        },
        "v43_baseline": reproduced_v43,
        "v44_baseline": v44_baseline,
        "evaluation": evaluation,
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run v4.6 walk-forward selective risk veto research"
    )
    parser.add_argument("--baseline-json", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("evidence/v46/historical.json"),
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
        "selected_is_disabled_baseline": report["selection"][
            "selected_is_disabled_baseline"
        ],
        "eligible_candidate_count": report["selection"][
            "eligible_candidate_count"
        ],
        "standard_return": evaluation["aggregate_standard_return"],
        "stress_return": evaluation["aggregate_stress_return"],
        "annualized_standard_return": evaluation[
            "annualized_standard_return"
        ],
        "maximum_drawdown": evaluation["maximum_drawdown"],
        "vetoed_assets": evaluation["vetoed_assets"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
