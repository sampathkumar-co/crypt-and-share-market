from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from tradebot.research.regime_ranking_v42 import (
    ASSETS,
    Dataset,
    REGIME_NAMES,
    STANDARD_ONE_WAY_COST,
    STRESS_ONE_WAY_COST,
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

SCHEMA_VERSION = "4.3-distributional-utility"
PROTOCOL_PATH = Path("research/V43_DISTRIBUTIONAL_UTILITY_RANKING_PROTOCOL.md")
CONTRACT_PATH = Path("research/V431_DISTRIBUTIONAL_UTILITY_IMPLEMENTATION_CONTRACT.md")
REGIME_ADDENDUM_PATH = Path("research/V432_DATE_LEVEL_REGIME_AGGREGATION_ADDENDUM.md")
LONG_REGIMES = (0, 1, 3)


class DistributionalUtilityV43Error(RuntimeError):
    pass


@dataclass
class Member:
    return3_model: Any
    return7_model: Any
    q20_model: Any
    rank_model: Any
    window_name: str


@dataclass
class Specialist:
    members: list[Member]


@dataclass
class Bundle:
    specialists: dict[int, Specialist]
    regime_models: list[Any]
    regime_window_names: list[str]
    config: dict[str, Any]
    panic_threshold: float
    utility_threshold: float
    q20_floor: float
    top_n: int
    disagreement_quantile: float
    disagreement_threshold: float
    feature_names: list[str]


def day(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


TRAIN_END = day("2025-06-30")
CALIBRATION_START = day("2025-07-01")
CALIBRATION_END = day("2025-09-30")
SEALED_WINDOWS = (
    ("sealed-1", day("2025-10-01"), day("2025-11-24")),
    ("sealed-2", day("2025-11-25"), day("2026-01-18")),
    ("sealed-3", day("2026-01-19"), day("2026-03-14")),
    ("sealed-4", day("2026-03-15"), day("2026-05-07")),
    ("sealed-5", day("2026-05-08"), day("2026-06-30")),
)
RECENCY_STARTS = {
    "full": None,
    "days720": day("2023-07-12"),
    "days360": day("2024-07-06"),
}


def date_mask(
    dataset: Dataset,
    start: datetime | None,
    end: datetime,
) -> np.ndarray:
    stamps = np.asarray([int(value.timestamp()) for value in dataset.dates])
    mask = stamps <= int(end.timestamp())
    if start is not None:
        mask &= stamps >= int(start.timestamp())
    return mask


def training_mask(dataset: Dataset) -> np.ndarray:
    return date_mask(dataset, None, TRAIN_END)


def calibration_mask(dataset: Dataset) -> np.ndarray:
    return date_mask(dataset, CALIBRATION_START, CALIBRATION_END)


def recency_masks(dataset: Dataset) -> dict[str, np.ndarray]:
    base = training_mask(dataset)
    result: dict[str, np.ndarray] = {}
    for name, start in RECENCY_STARTS.items():
        result[name] = base & date_mask(dataset, start, TRAIN_END)
    return result


def _fit_classifier(
    X: np.ndarray,
    y: np.ndarray,
    config: dict[str, Any],
    seed: int,
) -> Any:
    from sklearn.dummy import DummyClassifier
    from sklearn.ensemble import HistGradientBoostingClassifier

    unique = np.unique(y)
    if len(unique) == 1:
        return DummyClassifier(
            strategy="constant", constant=unique[0]
        ).fit(X, y)
    return HistGradientBoostingClassifier(
        **config,
        l2_regularization=0.1,
        random_state=seed,
    ).fit(X, y)


def _fit_member(
    dataset: Dataset,
    mask: np.ndarray,
    config: dict[str, Any],
    window_name: str,
) -> Member:
    from sklearn.ensemble import HistGradientBoostingRegressor

    X = dataset.X[mask]
    common = {
        **config,
        "l2_regularization": 0.1,
    }
    return Member(
        return3_model=HistGradientBoostingRegressor(
            **common, random_state=17
        ).fit(X, dataset.return3[mask]),
        return7_model=HistGradientBoostingRegressor(
            **common, random_state=19
        ).fit(X, dataset.return7[mask]),
        q20_model=HistGradientBoostingRegressor(
            **common,
            loss="quantile",
            quantile=0.20,
            random_state=23,
        ).fit(X, dataset.return3[mask]),
        rank_model=HistGradientBoostingRegressor(
            **common, random_state=29
        ).fit(X, dataset.rank3[mask]),
        window_name=window_name,
    )


def fit_family(
    dataset: Dataset,
    config: dict[str, Any],
) -> Bundle:
    masks = recency_masks(dataset)
    specialists: dict[int, Specialist] = {}
    for regime in LONG_REGIMES:
        members: list[Member] = []
        for name, mask in masks.items():
            specialist_mask = mask & (dataset.regimes == regime)
            if int(np.sum(specialist_mask)) < 250:
                continue
            members.append(_fit_member(
                dataset,
                specialist_mask,
                config,
                name,
            ))
        if len(members) >= 2:
            specialists[regime] = Specialist(members)

    regime_models: list[Any] = []
    regime_window_names: list[str] = []
    for index, (name, mask) in enumerate(masks.items()):
        if int(np.sum(mask)) < 500:
            continue
        regime_models.append(_fit_classifier(
            dataset.X[mask],
            dataset.regimes[mask],
            config,
            101 + index,
        ))
        regime_window_names.append(name)
    if len(regime_models) < 2:
        raise DistributionalUtilityV43Error(
            "fewer than two regime recency models"
        )
    return Bundle(
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


def aligned_probabilities(model: Any, X: np.ndarray) -> np.ndarray:
    raw = model.predict_proba(X)
    output = np.zeros((len(X), 4), dtype=float)
    for source_index, value in enumerate(model.classes_):
        output[:, int(value)] = raw[:, source_index]
    return output


def predict_components(bundle: Bundle, X: np.ndarray) -> dict[str, Any]:
    regime_members = np.stack([
        aligned_probabilities(model, X)
        for model in bundle.regime_models
    ])
    result: dict[str, Any] = {
        "regime_members": regime_members,
        "specialists": {},
    }
    for regime, specialist in bundle.specialists.items():
        return3_matrix = np.vstack([
            member.return3_model.predict(X)
            for member in specialist.members
        ])
        return7_matrix = np.vstack([
            member.return7_model.predict(X)
            for member in specialist.members
        ])
        q20_matrix = np.vstack([
            member.q20_model.predict(X)
            for member in specialist.members
        ])
        rank_matrix = np.vstack([
            member.rank_model.predict(X)
            for member in specialist.members
        ])
        result["specialists"][regime] = {
            "return3": np.mean(return3_matrix, axis=0),
            "return7": np.mean(return7_matrix, axis=0),
            "q20": np.mean(q20_matrix, axis=0),
            "rank": np.mean(rank_matrix, axis=0),
            "std_return3": np.std(return3_matrix, axis=0),
            "std_return7": np.std(return7_matrix, axis=0),
            "std_q20": np.std(q20_matrix, axis=0),
            "std_rank": np.std(rank_matrix, axis=0),
        }
    return result


def grouped_indexes(
    dataset: Dataset,
    mask: np.ndarray,
) -> dict[datetime, list[int]]:
    grouped: dict[datetime, list[int]] = {}
    for index in np.flatnonzero(mask):
        grouped.setdefault(dataset.dates[int(index)], []).append(int(index))
    return grouped


def date_contexts(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: Bundle,
    predictions: dict[str, Any],
) -> dict[datetime, dict[str, Any]]:
    result: dict[datetime, dict[str, Any]] = {}
    for stamp, indexes in grouped_indexes(dataset, mask).items():
        member_probabilities = np.mean(
            predictions["regime_members"][:, indexes, :],
            axis=1,
        )
        mean_probabilities = np.mean(member_probabilities, axis=0)
        std_probabilities = np.std(member_probabilities, axis=0)
        regime: int | None = None
        if mean_probabilities[2] >= bundle.panic_threshold:
            regime = 2
        else:
            available = sorted(bundle.specialists)
            if available:
                regime = sorted(
                    available,
                    key=lambda value: (-mean_probabilities[value], value),
                )[0]
        result[stamp] = {
            "indexes": indexes,
            "regime": regime,
            "mean_probabilities": mean_probabilities,
            "std_probabilities": std_probabilities,
        }
    return result


def candidate_metrics(
    specialist: dict[str, np.ndarray],
    index: int,
    regime_probability_std: float,
) -> dict[str, float]:
    disagreement = math.sqrt(
        float(specialist["std_return3"][index]) ** 2
        + float(specialist["std_return7"][index]) ** 2
        + float(specialist["std_q20"][index]) ** 2
        + (0.01 * float(specialist["std_rank"][index])) ** 2
        + (0.01 * regime_probability_std) ** 2
    )
    return3 = float(specialist["return3"][index])
    return7 = float(specialist["return7"][index])
    q20 = float(specialist["q20"][index])
    rank = float(specialist["rank"][index])
    utility = (
        0.55 * return3
        + 0.25 * return7
        + 0.20 * q20
        + 0.01 * (rank - 0.5)
        - 0.50 * disagreement
    )
    return {
        "return3": return3,
        "return7": return7,
        "q20": q20,
        "rank": rank,
        "disagreement": disagreement,
        "utility": utility,
    }


def calibration_disagreements(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: Bundle,
    predictions: dict[str, Any],
) -> np.ndarray:
    values: list[float] = []
    contexts = date_contexts(dataset, mask, bundle, predictions)
    for context in contexts.values():
        regime = context["regime"]
        if regime is None or regime == 2:
            continue
        specialist = predictions["specialists"][regime]
        probability_std = float(context["std_probabilities"][regime])
        for index in context["indexes"]:
            values.append(candidate_metrics(
                specialist,
                index,
                probability_std,
            )["disagreement"])
    return np.asarray(values, dtype=float)


def decisions_by_date(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: Bundle,
    predictions: dict[str, Any],
) -> dict[datetime, dict[str, Any]]:
    result: dict[datetime, dict[str, Any]] = {}
    contexts = date_contexts(dataset, mask, bundle, predictions)
    for stamp, context in contexts.items():
        regime = context["regime"]
        ranked: list[tuple[float, float, str, int]] = []
        if regime is not None and regime != 2:
            specialist = predictions["specialists"][regime]
            probability_std = float(context["std_probabilities"][regime])
            for index in context["indexes"]:
                metrics = candidate_metrics(
                    specialist,
                    index,
                    probability_std,
                )
                if metrics["return3"] <= 2.0 * STRESS_ONE_WAY_COST:
                    continue
                if metrics["return7"] <= 2.0 * STRESS_ONE_WAY_COST:
                    continue
                if metrics["q20"] < bundle.q20_floor:
                    continue
                if metrics["utility"] < bundle.utility_threshold:
                    continue
                if metrics["disagreement"] > bundle.disagreement_threshold:
                    continue
                ranked.append((
                    metrics["rank"],
                    metrics["utility"],
                    dataset.assets[index],
                    index,
                ))
        ordered = sorted(
            ranked,
            key=lambda item: (-item[0], -item[1], item[2]),
        )
        result[stamp] = {
            "regime": regime,
            "selected": [item[3] for item in ordered[:bundle.top_n]],
            "candidate_count": len(ranked),
            "panic_probability": float(
                context["mean_probabilities"][2]
            ),
        }
    return result


def simulate(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: Bundle,
    predictions: dict[str, Any],
    *,
    one_way_cost: float,
) -> dict[str, Any]:
    decisions = decisions_by_date(dataset, mask, bundle, predictions)
    index_map = {
        (dataset.dates[index], dataset.assets[index]): index
        for index in np.flatnonzero(mask)
    }
    cash = 1.0
    holdings = {asset: 0.0 for asset in ASSETS}
    holding_regime = {asset: 0 for asset in ASSETS}
    selected_assets: tuple[str, ...] = ()
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

    for stamp in sorted(decisions):
        equity_before = cash + sum(holdings.values())
        decision = decisions[stamp]
        panic = decision["regime"] == 2
        due = age >= 3
        target_assets = selected_assets
        if panic:
            target_assets = ()
        elif due:
            target_assets = tuple(
                dataset.assets[index]
                for index in decision["selected"]
            )

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
                cost = one_way_cost * traded
                cash -= cost
                turnover += traded
                action_count += 1
            cash += sum(
                holdings[asset] - target_values[asset]
                for asset in ASSETS
            )
            holdings = target_values
            selected_assets = target_assets
            selected_ever.update(target_assets)
            for asset in target_assets:
                holding_regime[asset] = int(decision["regime"])
            if due or (panic and changed):
                age = 0

        equity_open = cash + sum(holdings.values())
        maximum_gross_exposure = max(
            maximum_gross_exposure,
            sum(holdings.values()) / max(equity_open, 1e-12),
        )
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
        cost = one_way_cost * liquidation
        cash += liquidation - cost
        turnover += liquidation
        holdings = {asset: 0.0 for asset in ASSETS}
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
        "asset_contribution": asset_contribution,
        "regime_contribution": regime_contribution,
        "daily_returns": daily_returns,
        "decision_count": len(decisions),
        "maximum_gross_exposure": maximum_gross_exposure,
        "maximum_target_exposure": maximum_target_exposure,
        "terminal_equity_before_liquidation": (
            terminal_equity_before_liquidation
        ),
    }


def configured_bundle(
    base: Bundle,
    *,
    panic_threshold: float,
    utility_threshold: float,
    q20_floor: float,
    top_n: int,
    disagreement_quantile: float,
    disagreement_threshold: float,
) -> Bundle:
    return Bundle(
        specialists=base.specialists,
        regime_models=base.regime_models,
        regime_window_names=base.regime_window_names,
        config=base.config,
        panic_threshold=panic_threshold,
        utility_threshold=utility_threshold,
        q20_floor=q20_floor,
        top_n=top_n,
        disagreement_quantile=disagreement_quantile,
        disagreement_threshold=disagreement_threshold,
        feature_names=base.feature_names,
    )


def calibration_tie_key(
    score: float,
    summary: dict[str, Any],
    bundle: Bundle,
) -> tuple[float, ...]:
    return (
        score,
        -float(summary["maximum_drawdown"]),
        -float(summary["turnover"]),
        bundle.utility_threshold,
        bundle.q20_floor,
        -bundle.panic_threshold,
        -bundle.disagreement_quantile,
        -float(bundle.top_n),
        -float(bundle.config["learning_rate"]),
        -float(bundle.config["max_leaf_nodes"]),
    )


def train_bundle(dataset: Dataset) -> tuple[Bundle, dict[str, Any]]:
    train = training_mask(dataset)
    calibration = calibration_mask(dataset)
    if int(np.sum(train)) < 1000:
        raise DistributionalUtilityV43Error("insufficient training rows")
    if int(np.sum(calibration)) < 250:
        raise DistributionalUtilityV43Error("insufficient calibration rows")

    best: tuple[tuple[float, ...], Bundle, dict[str, Any]] | None = None
    for config in model_grid():
        base = fit_family(dataset, config)
        predictions = predict_components(base, dataset.X)
        for panic_threshold in (0.45, 0.55, 0.65):
            panic_bundle = configured_bundle(
                base,
                panic_threshold=panic_threshold,
                utility_threshold=0.004,
                q20_floor=-0.03,
                top_n=1,
                disagreement_quantile=0.75,
                disagreement_threshold=float("inf"),
            )
            disagreements = calibration_disagreements(
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
                            bundle = configured_bundle(
                                base,
                                panic_threshold=panic_threshold,
                                utility_threshold=utility_threshold,
                                q20_floor=q20_floor,
                                top_n=top_n,
                                disagreement_quantile=quantile,
                                disagreement_threshold=threshold,
                            )
                            summary = simulate(
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
                            key = calibration_tie_key(
                                score,
                                summary,
                                bundle,
                            )
                            if best is None or key > best[0]:
                                best = (key, bundle, summary)
    if best is None:
        raise DistributionalUtilityV43Error(
            "calibration produced no bundle"
        )
    selected = best[1]
    return selected, {
        "calibration_score": best[0][0],
        "calibration_summary": best[2],
        "config": selected.config,
        "panic_threshold": selected.panic_threshold,
        "utility_threshold": selected.utility_threshold,
        "q20_floor": selected.q20_floor,
        "top_n": selected.top_n,
        "disagreement_quantile": selected.disagreement_quantile,
        "disagreement_threshold": selected.disagreement_threshold,
        "available_specialists": [
            REGIME_NAMES[value]
            for value in sorted(selected.specialists)
        ],
        "regime_windows": selected.regime_window_names,
    }


def evaluate_sealed(
    dataset: Dataset,
    bundle: Bundle,
) -> dict[str, Any]:
    predictions = predict_components(bundle, dataset.X)
    windows: list[dict[str, Any]] = []
    for name, start, end in SEALED_WINDOWS:
        mask = date_mask(dataset, start, end)
        standard = simulate(
            dataset,
            mask,
            bundle,
            predictions,
            one_way_cost=STANDARD_ONE_WAY_COST,
        )
        stress = simulate(
            dataset,
            mask,
            bundle,
            predictions,
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
    }
    historical_only = all(
        value
        for key, value in gates.items()
        if key not in {
            "independent_source_replication",
            "current_market_smoke",
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
        "maximum_positive_asset_share": asset_share,
        "maximum_positive_window_share": window_share,
        "maximum_positive_regime_share": regime_share,
        "standard_window_returns": standard_returns,
        "stress_window_returns": stress_returns,
        "gates": gates,
        "status": (
            "HISTORICAL_BREAKTHROUGH_PENDING_REPLICATION_AND_SMOKE"
            if historical_only
            else "NOT_YET_HISTORICAL_BREAKTHROUGH"
        ),
    }


def member_to_state(value: Member) -> dict[str, Any]:
    return {
        "return3_model": value.return3_model,
        "return7_model": value.return7_model,
        "q20_model": value.q20_model,
        "rank_model": value.rank_model,
        "window_name": value.window_name,
    }


def bundle_to_state(value: Bundle) -> dict[str, Any]:
    return {
        "specialists": {
            str(regime): {
                "members": [
                    member_to_state(member)
                    for member in specialist.members
                ]
            }
            for regime, specialist in value.specialists.items()
        },
        "regime_models": value.regime_models,
        "regime_window_names": value.regime_window_names,
        "config": value.config,
        "panic_threshold": value.panic_threshold,
        "utility_threshold": value.utility_threshold,
        "q20_floor": value.q20_floor,
        "top_n": value.top_n,
        "disagreement_quantile": value.disagreement_quantile,
        "disagreement_threshold": value.disagreement_threshold,
        "feature_names": value.feature_names,
    }


def bundle_from_state(value: dict[str, Any]) -> Bundle:
    return Bundle(
        specialists={
            int(regime): Specialist([
                Member(**member)
                for member in specialist["members"]
            ])
            for regime, specialist in value["specialists"].items()
        },
        regime_models=value["regime_models"],
        regime_window_names=list(value["regime_window_names"]),
        config=dict(value["config"]),
        panic_threshold=float(value["panic_threshold"]),
        utility_threshold=float(value["utility_threshold"]),
        q20_floor=float(value["q20_floor"]),
        top_n=int(value["top_n"]),
        disagreement_quantile=float(
            value["disagreement_quantile"]
        ),
        disagreement_threshold=float(
            value["disagreement_threshold"]
        ),
        feature_names=list(value["feature_names"]),
    )


def save_bundle(path: Path, bundle: Bundle) -> None:
    import joblib

    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "schema_version": SCHEMA_VERSION,
        "authorizes_trading": False,
        "authorizes_shadow_paper": True,
        "bundle": bundle_to_state(bundle),
    }, path)


def bundle_summary(bundle: Bundle) -> dict[str, Any]:
    return {
        "config": bundle.config,
        "panic_threshold": bundle.panic_threshold,
        "utility_threshold": bundle.utility_threshold,
        "q20_floor": bundle.q20_floor,
        "top_n": bundle.top_n,
        "disagreement_quantile": bundle.disagreement_quantile,
        "disagreement_threshold": bundle.disagreement_threshold,
        "available_specialists": [
            REGIME_NAMES[value]
            for value in sorted(bundle.specialists)
        ],
        "specialist_member_windows": {
            REGIME_NAMES[regime]: [
                member.window_name
                for member in specialist.members
            ]
            for regime, specialist in bundle.specialists.items()
        },
        "regime_windows": bundle.regime_window_names,
    }


def run_campaign(
    states: dict[str, Any] | None = None,
    source_report: dict[str, Any] | None = None,
    *,
    monthly_workers: int = 24,
    metrics_workers: int = 48,
) -> tuple[dict[str, Any], Bundle]:
    if states is None:
        states, source_report = load_all_sources(
            monthly_workers=monthly_workers,
            metrics_workers=metrics_workers,
        )
    if source_report is None:
        source_report = {"schema_version": "synthetic-v43-source"}
    dataset = build_dataset(states)
    bundle, calibration = train_bundle(dataset)
    evaluation = evaluate_sealed(dataset, bundle)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_iso(datetime.now(timezone.utc)),
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": True,
        "universe": list(ASSETS),
        "source": source_report,
        "dataset": {
            "row_count": len(dataset.X),
            "date_count": len(set(dataset.dates)),
            "first_date": utc_iso(min(dataset.dates)),
            "last_date": utc_iso(max(dataset.dates)),
            "feature_count": len(dataset.feature_names),
            "training_end": utc_iso(TRAIN_END),
            "calibration_start": utc_iso(CALIBRATION_START),
            "calibration_end": utc_iso(CALIBRATION_END),
        },
        "protocol_sha256": file_sha256(PROTOCOL_PATH),
        "implementation_contract_sha256": file_sha256(CONTRACT_PATH),
        "regime_aggregation_sha256": file_sha256(
            REGIME_ADDENDUM_PATH
        ),
        "bundle": bundle_summary(bundle),
        "calibration": calibration,
        "evaluation": evaluation,
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report, bundle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run frozen v4.3 distributional-utility paper research"
        )
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("evidence/v43/historical.json"),
    )
    parser.add_argument(
        "--bundle-out",
        type=Path,
        default=Path("evidence/v43/bundle.joblib"),
    )
    parser.add_argument("--monthly-workers", type=int, default=24)
    parser.add_argument("--metrics-workers", type=int, default=48)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report, bundle = run_campaign(
        monthly_workers=max(1, args.monthly_workers),
        metrics_workers=max(1, args.metrics_workers),
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    save_bundle(args.bundle_out, bundle)
    print(json.dumps({
        "status": report["evaluation"]["status"],
        "report_sha256": report["report_sha256"],
        "standard_return": report["evaluation"][
            "aggregate_standard_return"
        ],
        "stress_return": report["evaluation"][
            "aggregate_stress_return"
        ],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
