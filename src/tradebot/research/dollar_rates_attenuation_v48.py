from __future__ import annotations

import argparse
import hashlib
import json
import platform
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from tradebot.research import distributional_utility_v43 as v43
from tradebot.research import macro_liquidity_state_v47 as v47
from tradebot.research import walk_forward_selective_veto_v46 as v46
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
    positive_share,
)
from tradebot.research.regime_ranking_v42_sources import (
    canonical_json,
    load_all_sources,
    utc_iso,
)

SCHEMA_VERSION = "4.8-dollar-rates-attenuation"
PROTOCOL_PATH = Path("research/V48_DOLLAR_RATES_ATTENUATION_PROTOCOL.md")
CONTRACT_PATH = Path(
    "research/V481_DOLLAR_RATES_ATTENUATION_IMPLEMENTATION_CONTRACT.md"
)
FAMILY = "dollar_rates"
ATTENUATION_MULTIPLIERS = (0.25, 0.50, 0.75)
ACTIVE_THRESHOLDS = tuple(
    value for value in v47.THRESHOLD_GRID if value is not None
)


class DollarRatesAttenuationV48Error(RuntimeError):
    pass


@dataclass(frozen=True)
class AttenuationFoldResult:
    fold: str
    multiplier: float
    threshold: float | None
    training_date_count: int
    positive_label_share: float | None
    calibration_months: list[dict[str, Any]]
    calibration_minimum_excess: float
    calibration_compounded_excess: float
    validation_baseline: dict[str, Any]
    validation_attenuated: dict[str, Any]
    validation_excess: float


def calendar_month_blocks(
    start: datetime,
    end: datetime,
) -> list[tuple[str, datetime, datetime]]:
    blocks: list[tuple[str, datetime, datetime]] = []
    cursor = start.replace(day=1)
    while cursor <= end:
        if cursor.month == 12:
            next_month = cursor.replace(
                year=cursor.year + 1,
                month=1,
            )
        else:
            next_month = cursor.replace(month=cursor.month + 1)
        block_start = max(start, cursor)
        block_end = min(end, next_month - timedelta(days=1))
        blocks.append((cursor.strftime("%Y-%m"), block_start, block_end))
        cursor = next_month
    return blocks


def attenuation_decisions(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    probabilities: dict[datetime, float],
    threshold: float | None,
    multiplier: float,
) -> dict[datetime, dict[str, Any]]:
    if not 0.0 <= multiplier <= 1.0:
        raise DollarRatesAttenuationV48Error(
            f"target multiplier is outside [0, 1]: {multiplier}"
        )
    baseline = v43.decisions_by_date(dataset, mask, bundle, predictions)
    result: dict[datetime, dict[str, Any]] = {}
    for stamp, decision in baseline.items():
        probability = float(probabilities[stamp])
        applied = 1.0
        if (
            threshold is not None
            and decision["regime"] != 2
            and decision["selected"]
            and probability < threshold
        ):
            applied = multiplier
        result[stamp] = {
            **decision,
            "macro_probability": probability,
            "macro_threshold": threshold,
            "target_multiplier": applied,
        }
    return result


def simulate_attenuation(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    cash_history: v44.CashRateHistory,
    probabilities: dict[datetime, float],
    threshold: float | None,
    multiplier: float,
    *,
    one_way_cost: float,
) -> dict[str, Any]:
    decisions = attenuation_decisions(
        dataset,
        mask,
        bundle,
        predictions,
        probabilities,
        threshold,
        multiplier,
    )
    index_map = {
        (dataset.dates[index], dataset.assets[index]): index
        for index in np.flatnonzero(mask)
    }
    cash = 1.0
    holdings = {asset: 0.0 for asset in ASSETS}
    holding_regime = {asset: 0 for asset in ASSETS}
    selected_assets: tuple[str, ...] = ()
    selected_ever: set[str] = set()
    attenuated_ever: set[str] = set()
    peak = 1.0
    maximum_drawdown = 0.0
    turnover = 0.0
    action_count = 0
    age = 3
    maximum_gross_exposure = 0.0
    maximum_target_exposure = 0.0
    maximum_selected_cardinality = 0
    attenuated_decision_count = 0
    minimum_applied_multiplier = 1.0
    maximum_applied_multiplier = 1.0
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    regime_contribution = {
        name: 0.0 for name in REGIME_NAMES.values()
    }
    cash_contribution = 0.0

    for stamp in sorted(decisions):
        equity_before = cash + sum(holdings.values())
        decision = decisions[stamp]
        panic = decision["regime"] == 2
        due = age >= 3
        target_assets = selected_assets
        target_multiplier = 1.0
        if panic:
            target_assets = ()
            target_multiplier = 0.0
        elif due:
            target_assets = tuple(
                dataset.assets[index] for index in decision["selected"]
            )
            target_multiplier = float(decision["target_multiplier"])
            if target_assets and target_multiplier < 1.0:
                attenuated_decision_count += 1
                attenuated_ever.update(target_assets)
                minimum_applied_multiplier = min(
                    minimum_applied_multiplier,
                    target_multiplier,
                )
            maximum_applied_multiplier = max(
                maximum_applied_multiplier,
                target_multiplier,
            )
        maximum_selected_cardinality = max(
            maximum_selected_cardinality,
            len(target_assets),
        )

        if panic or due:
            target_values = {
                asset: (
                    0.05 * target_multiplier * equity_before
                    if asset in target_assets
                    else 0.0
                )
                for asset in ASSETS
            }
            baseline_limit = 0.05 * equity_before
            if any(
                value > baseline_limit + 1e-12
                for value in target_values.values()
            ):
                raise DollarRatesAttenuationV48Error(
                    "attenuation increased a target above baseline"
                )
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
                holdings[asset] - target_values[asset]
                for asset in ASSETS
            )
            holdings = target_values
            selected_assets = target_assets
            selected_ever.update(
                asset for asset in target_assets if target_values[asset] > 0.0
            )
            for asset in target_assets:
                if target_values[asset] > 0.0:
                    holding_regime[asset] = int(decision["regime"])
            if due or (panic and changed):
                age = 0

        equity_open = cash + sum(holdings.values())
        maximum_gross_exposure = max(
            maximum_gross_exposure,
            sum(holdings.values()) / max(equity_open, 1e-12),
        )
        _, annual_rate = v44.prior_known_annual_rate(cash_history, stamp)
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
            regime_contribution[
                REGIME_NAMES[holding_regime[asset]]
            ] += contribution
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
        "attenuated_assets": sorted(attenuated_ever),
        "attenuated_decision_count": attenuated_decision_count,
        "minimum_applied_multiplier": minimum_applied_multiplier,
        "maximum_applied_multiplier": maximum_applied_multiplier,
        "asset_contribution": asset_contribution,
        "regime_contribution": regime_contribution,
        "cash_contribution": cash_contribution,
        "decision_count": len(decisions),
        "maximum_gross_exposure": maximum_gross_exposure,
        "maximum_target_exposure": maximum_target_exposure,
        "maximum_selected_cardinality": maximum_selected_cardinality,
        "never_added_asset": True,
        "never_increased_target": maximum_target_exposure <= 0.1000001,
    }


def normalized_baseline(summary: dict[str, Any]) -> dict[str, Any]:
    result = dict(summary)
    result.setdefault("attenuated_assets", [])
    result.setdefault("attenuated_decision_count", 0)
    result.setdefault("minimum_applied_multiplier", 1.0)
    result.setdefault("maximum_applied_multiplier", 1.0)
    result.setdefault(
        "maximum_selected_cardinality",
        min(2, len(result.get("selected_assets", []))),
    )
    result.setdefault("never_added_asset", True)
    result.setdefault("never_increased_target", True)
    return result


def _compounded_excess(
    attenuated: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
) -> float:
    attenuated_growth = float(np.prod([
        1.0 + float(value["net_return"])
        for value in attenuated
    ]))
    baseline_growth = float(np.prod([
        1.0 + float(value["net_return"])
        for value in baseline
    ]))
    return attenuated_growth / max(baseline_growth, 1e-12) - 1.0


def calibrate_threshold(
    dataset: Dataset,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    cash_history: v44.CashRateHistory,
    probabilities: dict[datetime, float],
    multiplier: float,
    start: datetime,
    end: datetime,
) -> tuple[float, list[dict[str, Any]], tuple[Any, ...]]:
    best: tuple[tuple[Any, ...], float, list[dict[str, Any]]] | None = None
    blocks = calendar_month_blocks(start, end)
    for threshold in ACTIVE_THRESHOLDS:
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
            attenuated = simulate_attenuation(
                dataset,
                mask,
                bundle,
                predictions,
                cash_history,
                probabilities,
                threshold,
                multiplier,
                one_way_cost=STANDARD_ONE_WAY_COST,
            )
            month_results.append({
                "name": name,
                "start": utc_iso(block_start),
                "end": utc_iso(block_end),
                "baseline": baseline,
                "attenuated": attenuated,
                "excess": (
                    float(attenuated["net_return"])
                    - float(baseline["net_return"])
                ),
            })
        excess = [float(value["excess"]) for value in month_results]
        key = (
            min(excess),
            _compounded_excess(
                [value["attenuated"] for value in month_results],
                [value["baseline"] for value in month_results],
            ),
            -max(
                float(value["attenuated"]["maximum_drawdown"])
                for value in month_results
            ),
            -sum(
                float(value["attenuated"]["turnover"])
                for value in month_results
            ),
            -sum(
                int(value["attenuated"]["target_changing_actions"])
                for value in month_results
            ),
            -sum(
                int(value["attenuated"]["attenuated_decision_count"])
                for value in month_results
            ),
            -float(threshold),
        )
        if best is None or key > best[0]:
            best = (key, float(threshold), month_results)
    if best is None:
        raise DollarRatesAttenuationV48Error(
            "threshold calibration produced no active candidate"
        )
    return best[1], best[2], best[0]


def fit_and_evaluate_fold(
    dataset: Dataset,
    macro_by_date: dict[datetime, np.ndarray],
    cash_history: v44.CashRateHistory,
    fold: dict[str, Any],
    multiplier: float,
) -> AttenuationFoldResult:
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
        multiplier,
        spec.base_calibration_start,
        spec.base_calibration_end,
    )
    validation = simulate_attenuation(
        dataset,
        fold["validation_mask"],
        fold["bundle"],
        fold["predictions"],
        cash_history,
        probabilities,
        threshold,
        multiplier,
        one_way_cost=STANDARD_ONE_WAY_COST,
    )
    return AttenuationFoldResult(
        fold=spec.name,
        multiplier=multiplier,
        threshold=threshold,
        training_date_count=len(training_dates),
        positive_label_share=float(np.mean(y_train)),
        calibration_months=months,
        calibration_minimum_excess=float(key[0]),
        calibration_compounded_excess=float(key[1]),
        validation_baseline=fold["baseline"],
        validation_attenuated=validation,
        validation_excess=(
            float(validation["net_return"])
            - float(fold["baseline"]["net_return"])
        ),
    )


def compounded_validation_excess(
    results: list[AttenuationFoldResult],
) -> float:
    return _compounded_excess(
        [value.validation_attenuated for value in results],
        [value.validation_baseline for value in results],
    )


def multiplier_eligibility(
    multiplier: float,
    results: list[AttenuationFoldResult],
) -> tuple[bool, list[str]]:
    if multiplier == 1.0:
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
        int(value.validation_attenuated["target_changing_actions"])
        for value in results
    ) > sum(
        int(value.validation_baseline["target_changing_actions"])
        for value in results
    ):
        reasons.append("increased_actions")
    if sum(
        float(value.validation_attenuated["turnover"])
        for value in results
    ) > sum(
        float(value.validation_baseline["turnover"])
        for value in results
    ) + 1e-12:
        reasons.append("increased_turnover")
    if any(
        float(value.validation_attenuated["maximum_drawdown"])
        > float(value.validation_baseline["maximum_drawdown"]) + 0.0025
        for value in results
    ):
        reasons.append("drawdown_allowance_exceeded")
    if sum(
        int(value.validation_attenuated["attenuated_decision_count"])
        for value in results
    ) == 0:
        reasons.append("no_validation_attenuation")
    return not reasons, reasons


def multiplier_selection_key(
    multiplier: float,
    results: list[AttenuationFoldResult],
) -> tuple[Any, ...]:
    excess = [value.validation_excess for value in results]
    return (
        min(excess),
        sum(value > 0.0 for value in excess),
        compounded_validation_excess(results),
        min(
            float(value.validation_attenuated["net_return"])
            for value in results
        ),
        -max(
            float(value.validation_attenuated["maximum_drawdown"])
            for value in results
        ),
        -sum(
            float(value.validation_attenuated["turnover"])
            for value in results
        ),
        -sum(
            int(value.validation_attenuated["target_changing_actions"])
            for value in results
        ),
        -sum(
            int(value.validation_attenuated["attenuated_decision_count"])
            for value in results
        ),
        multiplier,
    )


def select_multiplier(
    multiplier_results: dict[float, list[AttenuationFoldResult]],
) -> tuple[float, dict[str, Any]]:
    if not multiplier_results:
        raise DollarRatesAttenuationV48Error(
            "multiplier selection requires active candidates"
        )
    exemplar = next(iter(multiplier_results.values()))
    disabled: list[AttenuationFoldResult] = []
    for value in exemplar:
        disabled.append(AttenuationFoldResult(
            fold=value.fold,
            multiplier=1.0,
            threshold=None,
            training_date_count=value.training_date_count,
            positive_label_share=value.positive_label_share,
            calibration_months=[],
            calibration_minimum_excess=0.0,
            calibration_compounded_excess=0.0,
            validation_baseline=value.validation_baseline,
            validation_attenuated=normalized_baseline(
                value.validation_baseline
            ),
            validation_excess=0.0,
        ))
    all_results = {1.0: disabled, **multiplier_results}
    best_active: tuple[tuple[Any, ...], float] | None = None
    candidates: list[dict[str, Any]] = []
    for multiplier, results in sorted(all_results.items()):
        eligible, reasons = multiplier_eligibility(multiplier, results)
        key = (
            multiplier_selection_key(multiplier, results)
            if eligible and multiplier != 1.0
            else None
        )
        candidates.append({
            "multiplier": multiplier,
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
                int(value.validation_attenuated[
                    "attenuated_decision_count"
                ])
                for value in results
            ),
            "folds": [asdict(value) for value in results],
        })
        if (
            multiplier != 1.0
            and eligible
            and (best_active is None or key > best_active[0])
        ):
            best_active = (key, multiplier)
    selected_multiplier = (
        best_active[1] if best_active is not None else 1.0
    )
    return selected_multiplier, {
        "selected_multiplier": selected_multiplier,
        "selected_is_disabled_baseline": selected_multiplier == 1.0,
        "selected_key": (
            list(best_active[0]) if best_active is not None else None
        ),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "folds": [
            asdict(value)
            for value in all_results[selected_multiplier]
        ],
    }


def fit_final_rule(
    dataset: Dataset,
    macro_by_date: dict[datetime, np.ndarray],
    cash_history: v44.CashRateHistory,
    bundle: v43.Bundle,
    multiplier: float,
) -> tuple[dict[datetime, float], float | None, dict[str, Any]]:
    if multiplier == 1.0:
        return (
            {stamp: 1.0 for stamp in set(dataset.dates)},
            None,
            {
                "multiplier": 1.0,
                "threshold": None,
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
        multiplier,
        v43.CALIBRATION_START,
        v43.CALIBRATION_END,
    )
    return probabilities, threshold, {
        "multiplier": multiplier,
        "threshold": threshold,
        "training_date_count": len(training_dates),
        "positive_label_share": float(np.mean(y_train)),
        "calibration_months": months,
        "calibration_minimum_excess": float(key[0]),
        "calibration_compounded_excess": float(key[1]),
        "attenuated_decision_count": sum(
            int(value["attenuated"]["attenuated_decision_count"])
            for value in months
        ),
    }


def evaluate_sealed(
    dataset: Dataset,
    bundle: v43.Bundle,
    cash_history: v44.CashRateHistory,
    probabilities: dict[datetime, float],
    threshold: float | None,
    multiplier: float,
) -> dict[str, Any]:
    predictions = v43.predict_components(bundle, dataset.X)
    windows: list[dict[str, Any]] = []
    for name, start, end in v43.SEALED_WINDOWS:
        mask = v43.date_mask(dataset, start, end)
        standard = simulate_attenuation(
            dataset,
            mask,
            bundle,
            predictions,
            cash_history,
            probabilities,
            threshold,
            multiplier,
            one_way_cost=STANDARD_ONE_WAY_COST,
        )
        stress = simulate_attenuation(
            dataset,
            mask,
            bundle,
            predictions,
            cash_history,
            probabilities,
            threshold,
            multiplier,
            one_way_cost=STRESS_ONE_WAY_COST,
        )
        days = len({
            dataset.dates[index]
            for index in np.flatnonzero(mask)
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
        float(value["standard"]["net_return"])
        for value in windows
    ]
    stress_returns = [
        float(value["stress"]["net_return"])
        for value in windows
    ]
    aggregate_standard = float(
        np.prod([1.0 + value for value in standard_returns]) - 1.0
    )
    aggregate_stress = float(
        np.prod([1.0 + value for value in stress_returns]) - 1.0
    )
    verification_days = sum(
        int(value["standard"]["verification_days"])
        for value in windows
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
        set(value["standard"]["selected_assets"])
        for value in windows
    ]))
    attenuated_assets = sorted(set().union(*[
        set(value["standard"]["attenuated_assets"])
        for value in windows
    ]))
    attenuated_decisions = sum(
        int(value["standard"]["attenuated_decision_count"])
        for value in windows
    )
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    regime_contribution = {
        name: 0.0 for name in REGIME_NAMES.values()
    }
    cash_contribution = 0.0
    for value in windows:
        cash_contribution += float(
            value["standard"]["cash_contribution"]
        )
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
        "attenuated_assets": attenuated_assets,
        "attenuated_decision_count": attenuated_decisions,
        "asset_contribution": asset_contribution,
        "regime_contribution": regime_contribution,
        "cash_contribution": cash_contribution,
        "maximum_positive_asset_share": asset_share,
        "maximum_positive_window_share": window_share,
        "maximum_positive_regime_share": regime_share,
        "standard_window_returns": standard_returns,
        "stress_window_returns": stress_returns,
        "gates": gates,
        "retrospective": True,
        "status": (
            "RETROSPECTIVE_HISTORICAL_BREAKTHROUGH_PENDING_REPLICATION_AND_SMOKE"
            if historical_only
            else "RETROSPECTIVE_NOT_YET_BREAKTHROUGH"
        ),
        "maximum_target_exposure": max(
            float(value["standard"]["maximum_target_exposure"])
            for value in windows
        ),
        "maximum_selected_cardinality": max(
            int(value["standard"]["maximum_selected_cardinality"])
            for value in windows
        ),
        "minimum_applied_multiplier": min(
            float(value["standard"]["minimum_applied_multiplier"])
            for value in windows
        ),
        "never_added_asset": all(
            bool(value["standard"]["never_added_asset"])
            and bool(value["stress"]["never_added_asset"])
            for value in windows
        ),
        "never_increased_target": all(
            bool(value["standard"]["never_increased_target"])
            and bool(value["stress"]["never_increased_target"])
            for value in windows
        ),
    }


def runtime_versions() -> dict[str, str]:
    import joblib
    import sklearn

    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scikit_learn": sklearn.__version__,
        "joblib": joblib.__version__,
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
        raise DollarRatesAttenuationV48Error(
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
    multiplier_results = {
        multiplier: [
            fit_and_evaluate_fold(
                dataset,
                macro_by_date,
                cash_history,
                fold,
                multiplier,
            )
            for fold in folds
        ]
        for multiplier in ATTENUATION_MULTIPLIERS
    }
    selected_multiplier, selection = select_multiplier(
        multiplier_results
    )
    probabilities, threshold, final_calibration = fit_final_rule(
        dataset,
        macro_by_date,
        cash_history,
        bundle,
        selected_multiplier,
    )
    evaluation = evaluate_sealed(
        dataset,
        bundle,
        cash_history,
        probabilities,
        threshold,
        selected_multiplier,
    )
    baseline_evaluation = v44_report["evaluation"]
    comparison = {
        "standard_return_uplift": (
            float(evaluation["aggregate_standard_return"])
            - float(baseline_evaluation["aggregate_standard_return"])
        ),
        "stress_return_uplift": (
            float(evaluation["aggregate_stress_return"])
            - float(baseline_evaluation["aggregate_stress_return"])
        ),
        "annualized_return_uplift": (
            float(evaluation["annualized_standard_return"])
            - float(baseline_evaluation["annualized_standard_return"])
        ),
        "actions_not_increased": (
            int(evaluation["target_changing_actions"])
            <= int(baseline_evaluation["target_changing_actions"])
        ),
        "maximum_target_exposure_not_increased": (
            float(evaluation["maximum_target_exposure"]) <= 0.1000001
        ),
        "never_added_asset": evaluation["never_added_asset"],
        "never_increased_target": evaluation[
            "never_increased_target"
        ],
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
        "runtime": runtime_versions(),
        "dataset": v44_report["dataset"],
        "macro_features": {
            "family": FAMILY,
            "feature_names": [
                v47.MACRO_FEATURE_NAMES[index]
                for index in v47.FAMILY_COLUMNS[FAMILY]
            ],
            "row_count": len(macro_matrix),
            "date_count": len(macro_by_date),
            "availability_rule": (
                "newest observation dated <= decision date - 1 day"
            ),
            "attenuation_multipliers": list(
                ATTENUATION_MULTIPLIERS
            ),
            "active_thresholds": list(ACTIVE_THRESHOLDS),
        },
        "protocol_sha256": file_sha256(PROTOCOL_PATH),
        "implementation_contract_sha256": file_sha256(CONTRACT_PATH),
        "v47_protocol_sha256": file_sha256(v47.PROTOCOL_PATH),
        "v47_report_sha256": (
            "99215db2c72c1792c771972a4efe290d"
            "d791d0be3764fd98675fc0ca7aeebdaf"
        ),
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
            "active_multiplier_count": len(
                ATTENUATION_MULTIPLIERS
            ),
            "final_v43_retrained_for_v48": False,
        },
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run v4.8 dollar/rates exposure attenuation"
    )
    parser.add_argument("--baseline-json", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("evidence/v48/historical.json"),
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
        "selected_multiplier": report["selection"][
            "selected_multiplier"
        ],
        "selected_threshold": report["final_calibration"][
            "threshold"
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
