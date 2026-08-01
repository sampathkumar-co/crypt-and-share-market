from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from tradebot.research import cross_exchange_confirmation_v48 as v48
from tradebot.research import distributional_utility_v43 as v43
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

SCHEMA_VERSION = "4.9-cross-exchange-target-ensemble"
PROTOCOL_PATH = Path(
    "research/V49_CROSS_EXCHANGE_TARGET_ENSEMBLE_PROTOCOL.md"
)
CONTRACT_PATH = Path(
    "research/"
    "V491_CROSS_EXCHANGE_TARGET_ENSEMBLE_IMPLEMENTATION_CONTRACT.md"
)
COMBINED_WEIGHTS = (0.25, 0.50, 0.75)


class CrossExchangeTargetEnsembleV49Error(RuntimeError):
    pass


@dataclass(frozen=True)
class EnsembleConfig:
    combined_weight: float

    @property
    def control_weight(self) -> float:
        return 1.0 - self.combined_weight


def candidate_configs() -> list[EnsembleConfig]:
    return [EnsembleConfig(value) for value in COMBINED_WEIGHTS]


def _target_components(
    equity: float,
    control_assets: tuple[str, ...],
    combined_assets: tuple[str, ...],
    config: EnsembleConfig,
) -> dict[str, dict[str, float]]:
    if not 0.0 <= config.combined_weight <= 1.0:
        raise CrossExchangeTargetEnsembleV49Error(
            f"invalid combined weight: {config.combined_weight}"
        )
    result = {
        "control": {
            asset: (
                0.05 * config.control_weight * equity
                if asset in control_assets
                else 0.0
            )
            for asset in ASSETS
        },
        "combined": {
            asset: (
                0.05 * config.combined_weight * equity
                if asset in combined_assets
                else 0.0
            )
            for asset in ASSETS
        },
    }
    exposure = sum(
        value
        for sleeve in result.values()
        for value in sleeve.values()
    ) / max(equity, 1e-12)
    if exposure > 0.1000001:
        raise CrossExchangeTargetEnsembleV49Error(
            f"target exposure exceeded 10%: {exposure}"
        )
    return result


def _totals(
    components: dict[str, dict[str, float]],
) -> dict[str, float]:
    return {
        asset: sum(
            float(components[sleeve][asset])
            for sleeve in ("control", "combined")
        )
        for asset in ASSETS
    }


def simulate_ensemble(
    base: Dataset,
    combined: Dataset,
    mask: np.ndarray,
    control_bundle: v43.Bundle,
    combined_bundle: v43.Bundle,
    cash_history: v44.CashRateHistory,
    config: EnsembleConfig,
    *,
    one_way_cost: float,
) -> dict[str, Any]:
    if len(base.X) != len(combined.X):
        raise CrossExchangeTargetEnsembleV49Error(
            "base and combined datasets have different row counts"
        )
    if base.dates != combined.dates or base.assets != combined.assets:
        raise CrossExchangeTargetEnsembleV49Error(
            "base and combined row identity differs"
        )
    control_predictions = v43.predict_components(
        control_bundle,
        base.X,
    )
    combined_predictions = v43.predict_components(
        combined_bundle,
        combined.X,
    )
    control_decisions = v43.decisions_by_date(
        base,
        mask,
        control_bundle,
        control_predictions,
    )
    combined_decisions = v43.decisions_by_date(
        combined,
        mask,
        combined_bundle,
        combined_predictions,
    )
    if set(control_decisions) != set(combined_decisions):
        raise CrossExchangeTargetEnsembleV49Error(
            "control and combined decision dates differ"
        )
    index_map = {
        (base.dates[index], base.assets[index]): index
        for index in np.flatnonzero(mask)
    }
    cash = 1.0
    components = {
        sleeve: {asset: 0.0 for asset in ASSETS}
        for sleeve in ("control", "combined")
    }
    selected = {
        "control": (),
        "combined": (),
    }
    selected_regime = {
        "control": 0,
        "combined": 0,
    }
    held_ever: set[str] = set()
    decision_selected_ever: set[str] = set()
    peak = 1.0
    maximum_drawdown = 0.0
    turnover = 0.0
    action_count = 0
    age = {
        "control": 3,
        "combined": 3,
    }
    sleeve_due_counts = {
        "control": 0,
        "combined": 0,
    }
    maximum_gross_exposure = 0.0
    maximum_target_exposure = 0.0
    daily_returns: list[float] = []
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    regime_contribution = {
        name: 0.0 for name in REGIME_NAMES.values()
    }
    cash_contribution = 0.0
    agreement_counts = {
        "same_selection": 0,
        "different_selection": 0,
        "one_or_both_cash": 0,
        "control_panic": 0,
        "combined_panic": 0,
    }

    for stamp in sorted(control_decisions):
        equity_before = cash + sum(_totals(components).values())
        decisions = {
            "control": control_decisions[stamp],
            "combined": combined_decisions[stamp],
        }
        datasets = {
            "control": base,
            "combined": combined,
        }
        panic = {
            sleeve: int(decision["regime"]) == 2
            for sleeve, decision in decisions.items()
        }
        due = {
            sleeve: age[sleeve] >= 3
            for sleeve in ("control", "combined")
        }
        if panic["control"]:
            agreement_counts["control_panic"] += 1
        if panic["combined"]:
            agreement_counts["combined_panic"] += 1

        update_required = any(due.values()) or any(panic.values())
        if update_required:
            next_selected = dict(selected)
            next_regime = dict(selected_regime)
            for sleeve in ("control", "combined"):
                decision = decisions[sleeve]
                dataset = datasets[sleeve]
                if panic[sleeve]:
                    next_selected[sleeve] = ()
                    next_regime[sleeve] = 2
                elif due[sleeve]:
                    next_selected[sleeve] = tuple(
                        dataset.assets[index]
                        for index in decision["selected"]
                    )
                    next_regime[sleeve] = int(decision["regime"])
                    decision_selected_ever.update(
                        next_selected[sleeve]
                    )
                    sleeve_due_counts[sleeve] += 1

            if any(due.values()):
                if (
                    not next_selected["control"]
                    or not next_selected["combined"]
                ):
                    agreement_counts["one_or_both_cash"] += 1
                elif (
                    next_selected["control"]
                    == next_selected["combined"]
                ):
                    agreement_counts["same_selection"] += 1
                else:
                    agreement_counts["different_selection"] += 1

            target_components = _target_components(
                equity_before,
                next_selected["control"],
                next_selected["combined"],
                config,
            )
            old_totals = _totals(components)
            new_totals = _totals(target_components)
            target_exposure = sum(new_totals.values()) / max(
                equity_before,
                1e-12,
            )
            maximum_target_exposure = max(
                maximum_target_exposure,
                target_exposure,
            )
            sleeve_changed = {
                sleeve: any(
                    abs(
                        target_components[sleeve][asset]
                        - components[sleeve][asset]
                    ) > 1e-12
                    for asset in ASSETS
                )
                for sleeve in ("control", "combined")
            }
            traded = sum(
                abs(new_totals[asset] - old_totals[asset])
                for asset in ASSETS
            )
            changed = traded > 1e-12
            if changed:
                cash -= one_way_cost * traded
                turnover += traded
                action_count += 1
            cash += sum(
                old_totals[asset] - new_totals[asset]
                for asset in ASSETS
            )
            components = target_components
            selected = next_selected
            selected_regime = next_regime
            held_ever.update(
                asset
                for asset, value in new_totals.items()
                if value > 0.0
            )
            for sleeve in ("control", "combined"):
                if due[sleeve] or (
                    panic[sleeve] and sleeve_changed[sleeve]
                ):
                    age[sleeve] = 0

        totals = _totals(components)
        equity_open = cash + sum(totals.values())
        maximum_gross_exposure = max(
            maximum_gross_exposure,
            sum(totals.values()) / max(equity_open, 1e-12),
        )
        _, annual_rate = v44.prior_known_annual_rate(
            cash_history,
            stamp,
        )
        cash_yield = cash * v44.annual_to_daily_rate(annual_rate)
        cash += cash_yield
        cash_contribution += cash_yield

        for sleeve in ("control", "combined"):
            regime_name = REGIME_NAMES[selected_regime[sleeve]]
            for asset in ASSETS:
                value = components[sleeve][asset]
                if value <= 0.0:
                    continue
                index = index_map[(stamp, asset)]
                asset_return = float(base.return1[index])
                contribution = value * asset_return
                components[sleeve][asset] *= 1.0 + asset_return
                asset_contribution[asset] += contribution
                regime_contribution[regime_name] += contribution

        equity_close = cash + sum(_totals(components).values())
        daily_returns.append(
            equity_close / max(equity_open, 1e-12) - 1.0
        )
        peak = max(peak, equity_close)
        maximum_drawdown = max(
            maximum_drawdown,
            1.0 - equity_close / peak,
        )
        for sleeve in ("control", "combined"):
            age[sleeve] += 1

    terminal_equity_before_liquidation = (
        cash + sum(_totals(components).values())
    )
    liquidation = sum(_totals(components).values())
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
        "selected_assets": sorted(held_ever),
        "decision_selected_assets": sorted(decision_selected_ever),
        "asset_contribution": asset_contribution,
        "regime_contribution": regime_contribution,
        "cash_contribution": cash_contribution,
        "daily_returns": daily_returns,
        "decision_count": len(control_decisions),
        "maximum_gross_exposure": maximum_gross_exposure,
        "maximum_target_exposure": maximum_target_exposure,
        "terminal_equity_before_liquidation": (
            terminal_equity_before_liquidation
        ),
        "agreement_counts": agreement_counts,
        "sleeve_due_counts": sleeve_due_counts,
        "combined_weight": config.combined_weight,
        "control_weight": config.control_weight,
    }


def _compound(values: list[float]) -> float:
    return float(np.prod([1.0 + value for value in values]) - 1.0)


def active_eligibility(
    fold_results: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    standard_excess = [
        float(value["ensemble_standard"]["net_return"])
        - float(value["control_standard"]["net_return"])
        for value in fold_results
    ]
    stress_excess = [
        float(value["ensemble_stress"]["net_return"])
        - float(value["control_stress"]["net_return"])
        for value in fold_results
    ]
    if sum(value > 0.0 for value in standard_excess) < 4:
        reasons.append("fewer_than_four_positive_standard_excess_folds")
    if _compound(standard_excess) <= 0.0:
        reasons.append("non_positive_compounded_standard_excess")
    if _compound(stress_excess) <= 0.0:
        reasons.append("non_positive_compounded_stress_excess")
    if min(standard_excess) < -0.005:
        reasons.append(
            "worst_standard_fold_excess_below_minus_0_50_percent"
        )
    for value in fold_results:
        ensemble_drawdown = max(
            float(value["ensemble_standard"]["maximum_drawdown"]),
            float(value["ensemble_stress"]["maximum_drawdown"]),
        )
        control_drawdown = max(
            float(value["control_standard"]["maximum_drawdown"]),
            float(value["control_stress"]["maximum_drawdown"]),
        )
        if ensemble_drawdown > control_drawdown + 0.005:
            reasons.append("drawdown_allowance_exceeded")
            break
    if any(
        float(value["ensemble_standard"]["maximum_target_exposure"])
        > 0.1000001
        or float(value["ensemble_stress"]["maximum_target_exposure"])
        > 0.1000001
        for value in fold_results
    ):
        reasons.append("target_exposure_exceeded")

    control_turnover = sum(
        float(value["control_standard"]["turnover"])
        for value in fold_results
    )
    ensemble_turnover = sum(
        float(value["ensemble_standard"]["turnover"])
        for value in fold_results
    )
    if ensemble_turnover > 1.25 * control_turnover + 1e-12:
        reasons.append("aggregate_turnover_exceeded")
    if any(
        float(value["ensemble_standard"]["turnover"])
        > (
            1.50 * float(value["control_standard"]["turnover"])
            + 0.05
        )
        for value in fold_results
    ):
        reasons.append("fold_turnover_exceeded")
    return not reasons, reasons


def _selection_key(
    fold_results: list[dict[str, Any]],
    config: EnsembleConfig,
) -> tuple[float, ...]:
    standard_excess = [
        float(value["ensemble_standard"]["net_return"])
        - float(value["control_standard"]["net_return"])
        for value in fold_results
    ]
    stress_excess = [
        float(value["ensemble_stress"]["net_return"])
        - float(value["control_stress"]["net_return"])
        for value in fold_results
    ]
    maximum_drawdown = max(
        max(
            float(value["ensemble_standard"]["maximum_drawdown"]),
            float(value["ensemble_stress"]["maximum_drawdown"]),
        )
        for value in fold_results
    )
    turnover = sum(
        float(value["ensemble_standard"]["turnover"])
        for value in fold_results
    )
    return (
        min(standard_excess),
        float(sum(value > 0.0 for value in standard_excess)),
        _compound(stress_excess),
        _compound(standard_excess),
        -maximum_drawdown,
        -turnover,
        -config.combined_weight,
    )


def run_walk_forward(
    base: Dataset,
    combined: Dataset,
    cash_history: v44.CashRateHistory,
) -> tuple[EnsembleConfig | None, dict[str, Any]]:
    folds: list[dict[str, Any]] = []
    results_by_weight: dict[float, list[dict[str, Any]]] = {
        value: [] for value in COMBINED_WEIGHTS
    }
    for fold in v46.WALK_FORWARD_FOLDS:
        control_bundle, control_training = v46.train_base_bundle(
            base,
            fold,
        )
        combined_bundle, combined_training = v46.train_base_bundle(
            combined,
            fold,
        )
        mask = v46.date_mask(
            base,
            fold.validation_start,
            fold.validation_end,
        )
        control_predictions = v43.predict_components(
            control_bundle,
            base.X,
        )
        combined_predictions = v43.predict_components(
            combined_bundle,
            combined.X,
        )
        control_standard = v44.simulate(
            base,
            mask,
            control_bundle,
            control_predictions,
            cash_history,
            one_way_cost=STANDARD_ONE_WAY_COST,
        )
        control_stress = v44.simulate(
            base,
            mask,
            control_bundle,
            control_predictions,
            cash_history,
            one_way_cost=STRESS_ONE_WAY_COST,
        )
        combined_standard = v44.simulate(
            combined,
            mask,
            combined_bundle,
            combined_predictions,
            cash_history,
            one_way_cost=STANDARD_ONE_WAY_COST,
        )
        combined_stress = v44.simulate(
            combined,
            mask,
            combined_bundle,
            combined_predictions,
            cash_history,
            one_way_cost=STRESS_ONE_WAY_COST,
        )
        ensemble_records: dict[str, Any] = {}
        for config in candidate_configs():
            ensemble_standard = simulate_ensemble(
                base,
                combined,
                mask,
                control_bundle,
                combined_bundle,
                cash_history,
                config,
                one_way_cost=STANDARD_ONE_WAY_COST,
            )
            ensemble_stress = simulate_ensemble(
                base,
                combined,
                mask,
                control_bundle,
                combined_bundle,
                cash_history,
                config,
                one_way_cost=STRESS_ONE_WAY_COST,
            )
            record = {
                "name": fold.name,
                "control_standard": control_standard,
                "control_stress": control_stress,
                "combined_standard": combined_standard,
                "combined_stress": combined_stress,
                "ensemble_standard": ensemble_standard,
                "ensemble_stress": ensemble_stress,
            }
            results_by_weight[config.combined_weight].append(record)
            ensemble_records[f"{config.combined_weight:.2f}"] = {
                "standard": ensemble_standard,
                "stress": ensemble_stress,
                "standard_excess": (
                    float(ensemble_standard["net_return"])
                    - float(control_standard["net_return"])
                ),
                "stress_excess": (
                    float(ensemble_stress["net_return"])
                    - float(control_stress["net_return"])
                ),
            }
        folds.append({
            "name": fold.name,
            "training_end": utc_iso(fold.training_end),
            "base_calibration_start": utc_iso(
                fold.base_calibration_start
            ),
            "base_calibration_end": utc_iso(
                fold.base_calibration_end
            ),
            "validation_start": utc_iso(fold.validation_start),
            "validation_end": utc_iso(fold.validation_end),
            "control_training": control_training,
            "combined_training": combined_training,
            "control_standard": control_standard,
            "control_stress": control_stress,
            "combined_standard": combined_standard,
            "combined_stress": combined_stress,
            "ensembles": ensemble_records,
        })

    candidates: list[dict[str, Any]] = [{
        "combined_weight": 0.0,
        "control_weight": 1.0,
        "disabled_baseline": True,
        "eligible": True,
        "ineligibility_reasons": [],
        "selection_key": None,
        "positive_standard_excess_folds": 0,
        "minimum_standard_excess": 0.0,
        "compounded_standard_excess": 0.0,
        "compounded_stress_excess": 0.0,
    }]
    eligible: list[
        tuple[tuple[float, ...], EnsembleConfig]
    ] = []
    for config in candidate_configs():
        values = results_by_weight[config.combined_weight]
        allowed, reasons = active_eligibility(values)
        standard_excess = [
            float(value["ensemble_standard"]["net_return"])
            - float(value["control_standard"]["net_return"])
            for value in values
        ]
        stress_excess = [
            float(value["ensemble_stress"]["net_return"])
            - float(value["control_stress"]["net_return"])
            for value in values
        ]
        key = _selection_key(values, config) if allowed else None
        candidates.append({
            "combined_weight": config.combined_weight,
            "control_weight": config.control_weight,
            "disabled_baseline": False,
            "eligible": allowed,
            "ineligibility_reasons": reasons,
            "selection_key": list(key) if key is not None else None,
            "positive_standard_excess_folds": sum(
                value > 0.0 for value in standard_excess
            ),
            "minimum_standard_excess": min(standard_excess),
            "compounded_standard_excess": _compound(
                standard_excess
            ),
            "compounded_stress_excess": _compound(stress_excess),
            "aggregate_control_turnover": sum(
                float(value["control_standard"]["turnover"])
                for value in values
            ),
            "aggregate_ensemble_turnover": sum(
                float(value["ensemble_standard"]["turnover"])
                for value in values
            ),
        })
        if allowed and key is not None:
            eligible.append((key, config))

    selected = max(eligible, key=lambda value: value[0])[1] \
        if eligible else None
    return selected, {
        "selected_config": (
            asdict(selected) if selected is not None else None
        ),
        "selected_is_disabled_baseline": selected is None,
        "walk_forward_fold_count": len(folds),
        "candidate_count": len(candidates),
        "eligible_active_candidate_count": len(eligible),
        "candidates": candidates,
        "folds": folds,
    }


def evaluate_final(
    base: Dataset,
    combined: Dataset,
    control_bundle: v43.Bundle,
    combined_bundle: v43.Bundle,
    cash_history: v44.CashRateHistory,
    config: EnsembleConfig,
    *,
    v44_baseline: dict[str, Any],
) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    for name, start, end in v43.SEALED_WINDOWS:
        mask = v46.date_mask(base, start, end)
        standard = simulate_ensemble(
            base,
            combined,
            mask,
            control_bundle,
            combined_bundle,
            cash_history,
            config,
            one_way_cost=STANDARD_ONE_WAY_COST,
        )
        stress = simulate_ensemble(
            base,
            combined,
            mask,
            control_bundle,
            combined_bundle,
            cash_history,
            config,
            one_way_cost=STRESS_ONE_WAY_COST,
        )
        days = len({
            base.dates[index]
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
        float(value["standard"]["net_return"]) for value in windows
    ]
    stress_returns = [
        float(value["stress"]["net_return"]) for value in windows
    ]
    aggregate_standard = _compound(standard_returns)
    aggregate_stress = _compound(stress_returns)
    verification_days = sum(
        int(value["standard"]["verification_days"])
        for value in windows
    )
    annualized = (
        (1.0 + aggregate_standard) ** (
            365.0 / verification_days
        ) - 1.0
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
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    regime_contribution = {
        name: 0.0 for name in REGIME_NAMES.values()
    }
    cash_contribution = 0.0
    maximum_target_exposure = 0.0
    agreement_counts = {
        "same_selection": 0,
        "different_selection": 0,
        "one_or_both_cash": 0,
        "control_panic": 0,
        "combined_panic": 0,
    }
    for value in windows:
        standard = value["standard"]
        cash_contribution += float(standard["cash_contribution"])
        maximum_target_exposure = max(
            maximum_target_exposure,
            float(standard["maximum_target_exposure"]),
        )
        for key, count in standard["agreement_counts"].items():
            agreement_counts[key] += int(count)
        for asset, contribution in standard[
            "asset_contribution"
        ].items():
            asset_contribution[asset] += float(contribution)
        for regime, contribution in standard[
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
        "annualized_standard_at_least_five_percent": (
            annualized >= 0.05
        ),
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
        "asset_contribution": asset_contribution,
        "regime_contribution": regime_contribution,
        "cash_contribution": cash_contribution,
        "maximum_positive_asset_share": asset_share,
        "maximum_positive_window_share": window_share,
        "maximum_positive_regime_share": regime_share,
        "maximum_target_exposure": maximum_target_exposure,
        "agreement_counts": agreement_counts,
        "standard_window_returns": standard_returns,
        "stress_window_returns": stress_returns,
        "combined_weight": config.combined_weight,
        "control_weight": config.control_weight,
        "gates": gates,
        "v44_comparison": {
            "standard_return_change": (
                aggregate_standard
                - float(v44_baseline["aggregate_standard_return"])
            ),
            "stress_return_change": (
                aggregate_stress
                - float(v44_baseline["aggregate_stress_return"])
            ),
            "annualized_return_change": (
                annualized
                - float(v44_baseline["annualized_standard_return"])
            ),
            "maximum_drawdown_change": (
                maximum_drawdown
                - float(v44_baseline["maximum_drawdown"])
            ),
            "action_count_change": (
                actions
                - int(v44_baseline["target_changing_actions"])
            ),
        },
        "status": (
            "RETROSPECTIVE_HISTORICAL_BREAKTHROUGH_"
            "PENDING_REPLICATION_AND_SMOKE"
            if historical_only
            else "RETROSPECTIVE_NOT_YET_BREAKTHROUGH"
        ),
    }


def run_campaign(
    baseline_report: dict[str, Any],
    final_control_bundle: v43.Bundle,
    states: dict[str, Any] | None = None,
    source_report: dict[str, Any] | None = None,
    cash_history: v44.CashRateHistory | None = None,
    coinbase_history: v48.CoinbaseHistory | None = None,
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
        raise CrossExchangeTargetEnsembleV49Error(
            "current Binance source report is unavailable"
        )
    if canonical_json(source_report) != canonical_json(
        baseline_report["source"]
    ):
        raise CrossExchangeTargetEnsembleV49Error(
            "current Binance source inventory differs from frozen v4.3"
        )
    base = build_dataset(states)
    observed_dataset = {
        "row_count": len(base.X),
        "date_count": len(set(base.dates)),
        "first_date": utc_iso(min(base.dates)),
        "last_date": utc_iso(max(base.dates)),
        "feature_count": len(base.feature_names),
        "training_end": utc_iso(v43.TRAIN_END),
        "calibration_start": utc_iso(v43.CALIBRATION_START),
        "calibration_end": utc_iso(v43.CALIBRATION_END),
    }
    if canonical_json(observed_dataset) != canonical_json(
        baseline_report["dataset"]
    ):
        raise CrossExchangeTargetEnsembleV49Error(
            "current base dataset differs from frozen v4.3"
        )
    if canonical_json(
        v43.bundle_summary(final_control_bundle)
    ) != canonical_json(baseline_report["bundle"]):
        raise CrossExchangeTargetEnsembleV49Error(
            "final control bundle differs from frozen v4.3"
        )
    reproduced_v43 = v43.evaluate_sealed(
        base,
        final_control_bundle,
    )
    if canonical_json(reproduced_v43) != canonical_json(
        baseline_report["evaluation"]
    ):
        raise CrossExchangeTargetEnsembleV49Error(
            "final control bundle does not reproduce frozen v4.3"
        )
    if cash_history is None:
        cash_history = v44.load_cash_history()
    if coinbase_history is None:
        coinbase_history = v48.download_coinbase_history()
    combined = v48.augmented_dataset(
        base,
        states,
        coinbase_history,
        "combined",
    )
    selected, selection = run_walk_forward(
        base,
        combined,
        cash_history,
    )
    v44_baseline = v44.evaluate_sealed(
        base,
        final_control_bundle,
        cash_history,
        baseline=reproduced_v43,
    )

    if selected is None:
        final_combined_bundle = None
        final_combined_calibration = None
        evaluation = v44_baseline
    else:
        final_combined_bundle, final_combined_calibration = (
            v43.train_bundle(combined)
        )
        evaluation = evaluate_final(
            base,
            combined,
            final_control_bundle,
            final_combined_bundle,
            cash_history,
            selected,
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
        "coinbase_source": coinbase_history.source,
        "cash_source": cash_history.source,
        "runtime": v44.runtime_versions(),
        "base_dataset": observed_dataset,
        "combined_dataset": {
            "row_count": len(combined.X),
            "feature_count": len(combined.feature_names),
            "added_feature_count": len(
                v48.family_feature_names("combined")
            ),
            "feature_names_sha256": hashlib.sha256(
                canonical_json(
                    combined.feature_names
                ).encode("utf-8")
            ).hexdigest(),
        },
        "protocol_sha256": file_sha256(PROTOCOL_PATH),
        "implementation_contract_sha256": file_sha256(
            CONTRACT_PATH
        ),
        "implementation_sha256": file_sha256(
            Path(__file__).resolve()
        ),
        "baseline_report_sha256": baseline_report[
            "report_sha256"
        ],
        "baseline_bundle_sha256": baseline_bundle_sha256,
        "reproduction": {
            "source_inventory_exact": True,
            "dataset_metadata_exact": True,
            "bundle_summary_exact": True,
            "v43_evaluation_exact": True,
            "final_control_retrained_for_v49": False,
            "walk_forward_fold_count": len(
                v46.WALK_FORWARD_FOLDS
            ),
            "future_coinbase_observations_allowed": False,
            "post_simulation_metric_blending": False,
        },
        "selection": selection,
        "final_control_bundle": v43.bundle_summary(
            final_control_bundle
        ),
        "final_combined_bundle": (
            v43.bundle_summary(final_combined_bundle)
            if final_combined_bundle is not None
            else None
        ),
        "final_combined_calibration": (
            final_combined_calibration
        ),
        "v43_baseline": reproduced_v43,
        "v44_baseline": v44_baseline,
        "evaluation": evaluation,
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def parse_args(
    argv: list[str] | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run v4.9 cross-exchange target ensemble"
    )
    parser.add_argument(
        "--baseline-json",
        type=Path,
        required=True,
    )
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("evidence/v49/historical.json"),
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
    selection = report["selection"]
    print(json.dumps({
        "status": evaluation["status"],
        "report_sha256": report["report_sha256"],
        "selected_config": selection["selected_config"],
        "selected_is_disabled_baseline": selection[
            "selected_is_disabled_baseline"
        ],
        "eligible_active_candidate_count": selection[
            "eligible_active_candidate_count"
        ],
        "standard_return": evaluation[
            "aggregate_standard_return"
        ],
        "stress_return": evaluation["aggregate_stress_return"],
        "annualized_standard_return": evaluation[
            "annualized_standard_return"
        ],
        "maximum_drawdown": evaluation["maximum_drawdown"],
        "standard_return_change": (
            evaluation.get("v44_comparison", {}).get(
                "standard_return_change",
                0.0,
            )
        ),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
