from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from tradebot.research import distributional_utility_v43 as v43
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

SCHEMA_VERSION = "4.5-regime-diversified-utility"
PROTOCOL_PATH = Path("research/V45_REGIME_DIVERSIFIED_UTILITY_PROTOCOL.md")
CONTRACT_PATH = Path(
    "research/V451_REGIME_DIVERSIFIED_UTILITY_IMPLEMENTATION_CONTRACT.md"
)
CALIBRATION_BLOCKS = (
    ("calibration-A", v43.day("2025-07-01"), v43.day("2025-07-31")),
    ("calibration-B", v43.day("2025-08-01"), v43.day("2025-08-31")),
    ("calibration-C", v43.day("2025-09-01"), v43.day("2025-09-30")),
)


class RegimeDiversifiedUtilityV45Error(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    panic_threshold: float
    utility_threshold: float
    q20_floor: float
    dispersion_quantile: float
    dispersion_threshold: float
    downside_exclusion_count: int
    top_n: int
    entropy_penalty: float
    dispersion_penalty: float


def normalized_entropy(probabilities: np.ndarray) -> float:
    values = np.asarray(probabilities, dtype=float)
    values = values[np.isfinite(values) & (values > 0.0)]
    if len(values) <= 1:
        return 0.0
    values = values / float(np.sum(values))
    entropy = -float(np.sum(values * np.log(values)))
    return entropy / math.log(len(values))


def _available_weights(
    bundle: v43.Bundle,
    probabilities: np.ndarray,
) -> tuple[list[int], np.ndarray]:
    regimes = sorted(bundle.specialists)
    if not regimes:
        raise RegimeDiversifiedUtilityV45Error(
            "no non-panic specialists are available"
        )
    raw = np.asarray([max(float(probabilities[r]), 0.0) for r in regimes])
    total = float(np.sum(raw))
    if total <= 1e-15:
        raw = np.ones(len(regimes), dtype=float)
        total = float(len(regimes))
    return regimes, raw / total


def mixed_candidate_metrics(
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    context: dict[str, Any],
    index: int,
    *,
    entropy_penalty: float,
    dispersion_penalty: float,
) -> dict[str, Any]:
    regimes, weights = _available_weights(
        bundle,
        context["mean_probabilities"],
    )
    metrics_by_regime: dict[int, dict[str, float]] = {}
    for regime in regimes:
        specialist = predictions["specialists"][regime]
        metrics_by_regime[regime] = v43.candidate_metrics(
            specialist,
            index,
            float(context["std_probabilities"][regime]),
        )

    def weighted(key: str) -> float:
        return float(sum(
            weight * metrics_by_regime[regime][key]
            for regime, weight in zip(regimes, weights, strict=True)
        ))

    specialist_utilities = np.asarray([
        metrics_by_regime[regime]["utility"] for regime in regimes
    ], dtype=float)
    base_utility = float(np.dot(weights, specialist_utilities))
    dispersion = float(np.sqrt(np.dot(
        weights,
        np.square(specialist_utilities - base_utility),
    )))
    entropy = normalized_entropy(weights)
    utility = (
        base_utility
        - dispersion_penalty * dispersion
        - entropy_penalty * entropy
    )
    attribution_scores = [
        float(context["mean_probabilities"][regime])
        * max(metrics_by_regime[regime]["utility"], 0.0)
        for regime in regimes
    ]
    if max(attribution_scores, default=0.0) <= 0.0:
        attribution_scores = [
            float(context["mean_probabilities"][regime])
            for regime in regimes
        ]
    attribution_regime = sorted(
        zip(attribution_scores, regimes, strict=True),
        key=lambda item: (-item[0], item[1]),
    )[0][1]
    return {
        "return3": weighted("return3"),
        "return7": weighted("return7"),
        "q20": weighted("q20"),
        "rank": weighted("rank"),
        "member_disagreement": weighted("disagreement"),
        "base_utility": base_utility,
        "cross_regime_dispersion": dispersion,
        "regime_entropy": entropy,
        "utility": utility,
        "attribution_regime": attribution_regime,
        "specialist_utilities": {
            REGIME_NAMES[regime]: metrics_by_regime[regime]["utility"]
            for regime in regimes
        },
        "regime_weights": {
            REGIME_NAMES[regime]: float(weight)
            for regime, weight in zip(regimes, weights, strict=True)
        },
    }


def calibration_dispersion_values(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    *,
    panic_threshold: float,
) -> np.ndarray:
    values: list[float] = []
    contexts = v43.date_contexts(dataset, mask, bundle, predictions)
    for context in contexts.values():
        if float(context["mean_probabilities"][2]) >= panic_threshold:
            continue
        for index in context["indexes"]:
            metrics = mixed_candidate_metrics(
                bundle,
                predictions,
                context,
                index,
                entropy_penalty=0.0,
                dispersion_penalty=0.0,
            )
            values.append(float(metrics["cross_regime_dispersion"]))
    return np.asarray(values, dtype=float)


def decisions_by_date(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    config: Config,
) -> dict[datetime, dict[str, Any]]:
    result: dict[datetime, dict[str, Any]] = {}
    contexts = v43.date_contexts(dataset, mask, bundle, predictions)
    for stamp, context in contexts.items():
        panic_probability = float(context["mean_probabilities"][2])
        panic = panic_probability >= config.panic_threshold
        candidate_metrics_by_index: dict[int, dict[str, Any]] = {}
        if not panic:
            for index in context["indexes"]:
                candidate_metrics_by_index[index] = mixed_candidate_metrics(
                    bundle,
                    predictions,
                    context,
                    index,
                    entropy_penalty=config.entropy_penalty,
                    dispersion_penalty=config.dispersion_penalty,
                )

        excluded: set[int] = set()
        if config.downside_exclusion_count and candidate_metrics_by_index:
            ordered_downside = sorted(
                candidate_metrics_by_index,
                key=lambda index: (
                    candidate_metrics_by_index[index]["q20"],
                    dataset.assets[index],
                ),
            )
            excluded.update(
                ordered_downside[: config.downside_exclusion_count]
            )

        ranked: list[tuple[float, float, str, int, int]] = []
        for index, metrics in candidate_metrics_by_index.items():
            if index in excluded:
                continue
            if metrics["return3"] <= 2.0 * STRESS_ONE_WAY_COST:
                continue
            if metrics["return7"] <= 2.0 * STRESS_ONE_WAY_COST:
                continue
            if metrics["q20"] < config.q20_floor:
                continue
            if metrics["utility"] < config.utility_threshold:
                continue
            if (
                metrics["cross_regime_dispersion"]
                > config.dispersion_threshold
            ):
                continue
            ranked.append((
                float(metrics["rank"]),
                float(metrics["utility"]),
                dataset.assets[index],
                index,
                int(metrics["attribution_regime"]),
            ))
        ordered = sorted(
            ranked,
            key=lambda item: (-item[0], -item[1], item[2]),
        )
        selected = ordered[: config.top_n]
        result[stamp] = {
            "panic": panic,
            "regime": 2 if panic else None,
            "selected": [item[3] for item in selected],
            "selected_regimes": {
                dataset.assets[item[3]]: item[4] for item in selected
            },
            "candidate_count": len(ranked),
            "excluded_assets": sorted(
                dataset.assets[index] for index in excluded
            ),
            "panic_probability": panic_probability,
        }
    return result


def simulate(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    history: v44.CashRateHistory,
    config: Config,
    *,
    one_way_cost: float,
) -> dict[str, Any]:
    decisions = decisions_by_date(
        dataset,
        mask,
        bundle,
        predictions,
        config,
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
    peak = 1.0
    maximum_drawdown = 0.0
    turnover = 0.0
    action_count = 0
    age = 3
    maximum_gross_exposure = 0.0
    maximum_target_exposure = 0.0
    daily_returns: list[float] = []
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    regime_contribution = {
        name: 0.0 for name in REGIME_NAMES.values()
    }
    cash_contribution = 0.0
    excluded_assets: set[str] = set()

    for stamp in sorted(decisions):
        equity_before = cash + sum(holdings.values())
        decision = decisions[stamp]
        excluded_assets.update(decision["excluded_assets"])
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
            regime_name = REGIME_NAMES[holding_regime[asset]]
            regime_contribution[regime_name] += contribution
        equity_close = cash + sum(holdings.values())
        daily_returns.append(
            equity_close / max(equity_open, 1e-12) - 1.0
        )
        peak = max(peak, equity_close)
        maximum_drawdown = max(
            maximum_drawdown,
            1.0 - equity_close / peak,
        )
        age += 1

    terminal_equity_before_liquidation = cash + sum(holdings.values())
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
        "excluded_assets": sorted(excluded_assets),
        "asset_contribution": asset_contribution,
        "regime_contribution": regime_contribution,
        "cash_contribution": cash_contribution,
        "daily_returns": daily_returns,
        "decision_count": len(decisions),
        "maximum_gross_exposure": maximum_gross_exposure,
        "maximum_target_exposure": maximum_target_exposure,
        "terminal_equity_before_liquidation": (
            terminal_equity_before_liquidation
        ),
    }


def _compound(values: list[float]) -> float:
    return float(np.prod([1.0 + value for value in values]) - 1.0)


def calibration_key(
    block_summaries: list[dict[str, Any]],
    config: Config,
) -> tuple[float, ...]:
    returns = [float(value["net_return"]) for value in block_summaries]
    actions = sum(
        int(value["target_changing_actions"]) for value in block_summaries
    )
    combined_regimes = {name: 0.0 for name in REGIME_NAMES.values()}
    for summary in block_summaries:
        for name, contribution in summary["regime_contribution"].items():
            combined_regimes[name] += float(contribution)
    worst = min(returns)
    if actions < 6:
        worst -= 1.0
    return (
        worst,
        float(sum(value > 0.0 for value in returns)),
        _compound(returns),
        -max(float(value["maximum_drawdown"]) for value in block_summaries),
        -sum(float(value["turnover"]) for value in block_summaries),
        float(sum(value > 0.0 for value in combined_regimes.values())),
        -positive_share(list(combined_regimes.values())),
        config.utility_threshold,
        config.q20_floor,
        -float(config.top_n),
        -float(config.downside_exclusion_count),
        -config.entropy_penalty,
        -config.dispersion_penalty,
        -config.panic_threshold,
        -config.dispersion_quantile,
    )


def calibrate(
    dataset: Dataset,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    history: v44.CashRateHistory,
) -> tuple[Config, dict[str, Any]]:
    block_masks = [
        v43.date_mask(dataset, start, end)
        for _, start, end in CALIBRATION_BLOCKS
    ]
    calibration_mask = np.logical_or.reduce(block_masks)
    thresholds: dict[tuple[float, float], float] = {}
    for panic_threshold in (0.45, 0.55, 0.65):
        values = calibration_dispersion_values(
            dataset,
            calibration_mask,
            bundle,
            predictions,
            panic_threshold=panic_threshold,
        )
        if not len(values):
            continue
        for quantile in (0.75, 0.90):
            thresholds[(panic_threshold, quantile)] = float(
                np.quantile(values, quantile)
            )

    best: tuple[
        tuple[float, ...], Config, list[dict[str, Any]]
    ] | None = None
    for (
        panic_threshold,
        utility_threshold,
        q20_floor,
        dispersion_quantile,
        downside_exclusion_count,
        top_n,
        entropy_penalty,
        dispersion_penalty,
    ) in itertools.product(
        (0.45, 0.55, 0.65),
        (0.002, 0.004, 0.006, 0.008),
        (-0.03, -0.02, -0.01),
        (0.75, 0.90),
        (0, 1),
        (1, 2),
        (0.0, 0.0025, 0.0050),
        (0.25, 0.50, 0.75),
    ):
        threshold = thresholds.get((panic_threshold, dispersion_quantile))
        if threshold is None:
            continue
        config = Config(
            panic_threshold=panic_threshold,
            utility_threshold=utility_threshold,
            q20_floor=q20_floor,
            dispersion_quantile=dispersion_quantile,
            dispersion_threshold=threshold,
            downside_exclusion_count=downside_exclusion_count,
            top_n=top_n,
            entropy_penalty=entropy_penalty,
            dispersion_penalty=dispersion_penalty,
        )
        summaries = [
            simulate(
                dataset,
                mask,
                bundle,
                predictions,
                history,
                config,
                one_way_cost=STANDARD_ONE_WAY_COST,
            )
            for mask in block_masks
        ]
        key = calibration_key(summaries, config)
        if best is None or key > best[0]:
            best = (key, config, summaries)
    if best is None:
        raise RegimeDiversifiedUtilityV45Error(
            "blocked calibration produced no configuration"
        )
    selected = best[1]
    blocks = []
    for (name, start, end), summary in zip(
        CALIBRATION_BLOCKS,
        best[2],
        strict=True,
    ):
        blocks.append({
            "name": name,
            "start": utc_iso(start),
            "end": utc_iso(end),
            "summary": summary,
        })
    return selected, {
        "selection_key": list(best[0]),
        "chosen_config": asdict(selected),
        "blocks": blocks,
        "worst_block_return": min(
            float(value["summary"]["net_return"]) for value in blocks
        ),
        "positive_block_count": sum(
            float(value["summary"]["net_return"]) > 0.0
            for value in blocks
        ),
        "compounded_block_return": _compound([
            float(value["summary"]["net_return"]) for value in blocks
        ]),
    }


def evaluate_sealed(
    dataset: Dataset,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    history: v44.CashRateHistory,
    config: Config,
    *,
    v44_baseline: dict[str, Any],
) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    for name, start, end in v43.SEALED_WINDOWS:
        mask = v43.date_mask(dataset, start, end)
        standard = simulate(
            dataset,
            mask,
            bundle,
            predictions,
            history,
            config,
            one_way_cost=STANDARD_ONE_WAY_COST,
        )
        stress = simulate(
            dataset,
            mask,
            bundle,
            predictions,
            history,
            config,
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
    aggregate_standard = _compound(standard_returns)
    aggregate_stress = _compound(stress_returns)
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
    cash_contribution = sum(
        float(value["standard"]["cash_contribution"])
        for value in windows
    )
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    regime_contribution = {
        name: 0.0 for name in REGIME_NAMES.values()
    }
    for value in windows:
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
    bundle: v43.Bundle,
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
        raise RegimeDiversifiedUtilityV45Error(
            "current source report is unavailable"
        )
    if canonical_json(source_report) != canonical_json(
        baseline_report["source"]
    ):
        raise RegimeDiversifiedUtilityV45Error(
            "current source inventory differs from the frozen v4.3 report"
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
        raise RegimeDiversifiedUtilityV45Error(
            "current dataset metadata differs from the frozen v4.3 report"
        )
    if canonical_json(v43.bundle_summary(bundle)) != canonical_json(
        baseline_report["bundle"]
    ):
        raise RegimeDiversifiedUtilityV45Error(
            "provided bundle does not match the frozen v4.3 report"
        )
    reproduced_v43 = v43.evaluate_sealed(dataset, bundle)
    if canonical_json(reproduced_v43) != canonical_json(
        baseline_report["evaluation"]
    ):
        raise RegimeDiversifiedUtilityV45Error(
            "provided bundle does not exactly reproduce v4.3 evaluation"
        )
    if cash_history is None:
        cash_history = v44.load_cash_history()
    if min(cash_history.annual_rates) > min(dataset.dates):
        raise RegimeDiversifiedUtilityV45Error(
            "cash history starts after the research dataset"
        )

    predictions = v43.predict_components(bundle, dataset.X)
    config, calibration = calibrate(
        dataset,
        bundle,
        predictions,
        cash_history,
    )
    v44_baseline = v44.evaluate_sealed(
        dataset,
        bundle,
        cash_history,
        baseline=reproduced_v43,
    )
    evaluation = evaluate_sealed(
        dataset,
        bundle,
        predictions,
        cash_history,
        config,
        v44_baseline=v44_baseline,
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_iso(datetime.now(v43.timezone.utc)),
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
        "bundle": v43.bundle_summary(bundle),
        "reproduction": {
            "source_inventory_exact": True,
            "dataset_metadata_exact": True,
            "bundle_summary_exact": True,
            "v43_evaluation_exact": True,
            "v43_retrained_for_v45": False,
        },
        "calibration": calibration,
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
        description="Run retrospective v4.5 regime-diversified utility research"
    )
    parser.add_argument("--baseline-json", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("evidence/v45/historical.json"),
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
        "standard_return": evaluation["aggregate_standard_return"],
        "stress_return": evaluation["aggregate_stress_return"],
        "annualized_standard_return": evaluation[
            "annualized_standard_return"
        ],
        "maximum_drawdown": evaluation["maximum_drawdown"],
        "maximum_positive_regime_share": evaluation[
            "maximum_positive_regime_share"
        ],
        "chosen_config": report["calibration"]["chosen_config"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
