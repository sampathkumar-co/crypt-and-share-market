from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from tradebot.research import distributional_utility_v43 as v43
from tradebot.research import dollar_rates_attenuation_v48 as v48
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

SCHEMA_VERSION = "5.2-adversarial-alpha-funnel"
PROTOCOL_PATH = Path("research/V52_ADVERSARIAL_ALPHA_FUNNEL_PROTOCOL.md")
CONTRACT_PATH = Path(
    "research/V521_ADVERSARIAL_ALPHA_FUNNEL_IMPLEMENTATION_CONTRACT.md"
)
SEED = 5_202_026
RAW_HYPOTHESIS_COUNT = 100_000
HISTORY_WINDOWS = (20, 40, 60, 90, 120, 180)
LAGS = (1, 3, 5, 10)
THRESHOLDS = (0.10, 0.20, 0.30, 0.40, 0.60, 0.70, 0.80, 0.90)
EVENTS = ("low", "high", "cross_up", "cross_down")
PERSISTENCE_DAYS = (1, 3, 7, 14, 21)
MULTIPLIERS = (0.25, 0.50, 0.75)
TRANSFORMS = ("level", "delta", "acceleration", "local_vol", "distance")
PROXY_KEEP = 4_096
ATTACK_KEEP = 512
EXACT_KEEP = 64
DEEP_ATTACK_KEEP = 16
MAX_SHORTLIST = 3
INTERVENTION_PENALTY = 0.00002


class AdversarialAlphaFunnelV52Error(RuntimeError):
    pass


@dataclass(frozen=True)
class Hypothesis:
    family: str
    source: str
    transform: str
    history: int
    lag: int
    event: str
    threshold: float
    persistence: int
    multiplier: float


@dataclass
class FoldData:
    name: str
    panel_dates: list[datetime]
    validation_dates: list[datetime]
    validation_positions: np.ndarray
    panel: dict[str, np.ndarray]
    baseline: dict[str, Any]
    baseline_daily_returns: np.ndarray
    risky_daily_returns: np.ndarray
    cash_daily_returns: np.ndarray
    rebalance_mask: np.ndarray
    selected_rebalance_mask: np.ndarray
    bundle: v43.Bundle
    predictions: dict[str, Any]
    validation_mask: np.ndarray


@dataclass
class Candidate:
    hypothesis: Hypothesis
    fingerprint: str
    proxy: dict[str, Any]
    attacks: dict[str, Any] | None = None
    exact: dict[str, Any] | None = None
    deep_attacks: dict[str, Any] | None = None


def canonical_hypothesis(value: Hypothesis) -> str:
    return canonical_json(asdict(value))


def hypothesis_complexity(value: Hypothesis) -> tuple[Any, ...]:
    return (
        TRANSFORMS.index(value.transform),
        value.history,
        value.lag,
        EVENTS.index(value.event),
        value.persistence,
        1.0 - value.multiplier,
        canonical_hypothesis(value),
    )


MARKET_FEATURES = (
    "btc_return_30",
    "btc_volatility_30",
    "btc_above_sma_100",
    "market_return_7",
    "market_return_30",
    "breadth_20",
    "breadth_100",
    "dispersion_30",
    "median_funding",
    "median_oi_change_7",
    "average_correlation_30",
    "fraction_long_build",
    "fraction_liquidation",
    "fraction_recovery",
)

CROSS_FEATURES = (
    "spot_return_3",
    "spot_return_7",
    "spot_return_14",
    "spot_return_30",
    "spot_return_60",
    "basis_change_7",
    "basis_change_30",
    "funding_z_30",
    "oi_change_7",
    "oi_change_30",
    "spot_flow_mean_7",
    "flow_divergence",
    "volatility_7",
    "volatility_30",
    "volatility_90",
    "sma_distance_20",
    "sma_distance_50",
    "sma_distance_100",
    "efficiency_14",
    "efficiency_60",
)

CROSS_AGGREGATIONS = ("mean", "std", "range", "positive_breadth")


def source_family(name: str) -> str:
    lowered = name.lower()
    if lowered.startswith("model_"):
        return "model_confidence"
    if any(token in lowered for token in ("funding", "basis", "oi_", "flow", "long_build", "liquidation", "recovery")):
        return "positioning_flow"
    if any(token in lowered for token in ("volatility", "dispersion", "correlation")):
        return "volatility_structure"
    if any(token in lowered for token in ("breadth", "range", "rank", "leader", "gap")):
        return "breadth_leadership"
    if any(token in lowered for token in ("return", "sma", "efficiency", "trend")):
        return "trend_state"
    return "relative_reversal"


def grouped_by_date(dataset: Dataset, dates: list[datetime]) -> dict[datetime, list[int]]:
    allowed = set(dates)
    grouped: dict[datetime, list[int]] = {}
    for index, stamp in enumerate(dataset.dates):
        if stamp in allowed:
            grouped.setdefault(stamp, []).append(index)
    return grouped


def aggregate(values: np.ndarray, method: str) -> float:
    finite = values[np.isfinite(values)]
    if len(finite) == 0:
        return math.nan
    if method == "mean":
        return float(np.mean(finite))
    if method == "std":
        return float(np.std(finite))
    if method == "range":
        return float(np.max(finite) - np.min(finite))
    if method == "positive_breadth":
        return float(np.mean(finite > 0.0))
    raise AdversarialAlphaFunnelV52Error(f"unknown aggregation: {method}")


def market_panel(
    dataset: Dataset,
    dates: list[datetime],
) -> dict[str, np.ndarray]:
    feature_indexes = {name: index for index, name in enumerate(dataset.feature_names)}
    grouped = grouped_by_date(dataset, dates)
    panel: dict[str, list[float]] = {
        name: [] for name in MARKET_FEATURES
    }
    for feature in CROSS_FEATURES:
        for method in CROSS_AGGREGATIONS:
            panel[f"{method}:{feature}"] = []
    for stamp in dates:
        indexes = grouped.get(stamp, [])
        if not indexes:
            for values in panel.values():
                values.append(math.nan)
            continue
        first = indexes[0]
        for name in MARKET_FEATURES:
            panel[name].append(float(dataset.X[first, feature_indexes[name]]))
        for feature in CROSS_FEATURES:
            values = dataset.X[indexes, feature_indexes[feature]]
            for method in CROSS_AGGREGATIONS:
                panel[f"{method}:{feature}"].append(
                    aggregate(np.asarray(values, dtype=float), method)
                )
    return {
        name: np.asarray(values, dtype=float)
        for name, values in panel.items()
    }


def model_panel(
    dataset: Dataset,
    dates: list[datetime],
    mask: np.ndarray,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
) -> dict[str, np.ndarray]:
    contexts = v43.date_contexts(dataset, mask, bundle, predictions)
    decisions = v43.decisions_by_date(dataset, mask, bundle, predictions)
    names = (
        "model_panic_probability",
        "model_regime_probability",
        "model_regime_disagreement",
        "model_candidate_count",
        "model_selected_return3",
        "model_selected_return7",
        "model_selected_q20",
        "model_selected_rank",
        "model_selected_utility",
        "model_selected_disagreement",
        "model_top_utility",
        "model_utility_gap",
        "model_top_rank",
        "model_rank_gap",
    )
    output = {name: [] for name in names}
    for stamp in dates:
        context = contexts.get(stamp)
        decision = decisions.get(stamp)
        if context is None or decision is None:
            for values in output.values():
                values.append(math.nan)
            continue
        regime = context["regime"]
        output["model_panic_probability"].append(
            float(context["mean_probabilities"][2])
        )
        if regime is None:
            output["model_regime_probability"].append(math.nan)
            output["model_regime_disagreement"].append(math.nan)
        else:
            output["model_regime_probability"].append(
                float(context["mean_probabilities"][regime])
            )
            output["model_regime_disagreement"].append(
                float(context["std_probabilities"][regime])
            )
        output["model_candidate_count"].append(
            float(decision["candidate_count"])
        )
        metric_names = (
            "return3", "return7", "q20", "rank", "utility", "disagreement"
        )
        selected_metrics: dict[str, float] | None = None
        ranked_metrics: list[dict[str, float]] = []
        if regime is not None and regime != 2:
            specialist = predictions["specialists"][regime]
            probability_std = float(context["std_probabilities"][regime])
            for index in context["indexes"]:
                metrics = v43.candidate_metrics(
                    specialist, index, probability_std
                )
                ranked_metrics.append(metrics)
                if decision["selected"] and index == decision["selected"][0]:
                    selected_metrics = metrics
        for name in metric_names:
            output[f"model_selected_{name}"].append(
                float(selected_metrics[name])
                if selected_metrics is not None else math.nan
            )
        ranked_utility = sorted(
            (float(value["utility"]) for value in ranked_metrics), reverse=True
        )
        ranked_rank = sorted(
            (float(value["rank"]) for value in ranked_metrics), reverse=True
        )
        output["model_top_utility"].append(
            ranked_utility[0] if ranked_utility else math.nan
        )
        output["model_utility_gap"].append(
            ranked_utility[0] - ranked_utility[1]
            if len(ranked_utility) >= 2 else math.nan
        )
        output["model_top_rank"].append(
            ranked_rank[0] if ranked_rank else math.nan
        )
        output["model_rank_gap"].append(
            ranked_rank[0] - ranked_rank[1]
            if len(ranked_rank) >= 2 else math.nan
        )
    return {
        name: np.asarray(values, dtype=float)
        for name, values in output.items()
    }



def baseline_schedule(
    decisions: dict[datetime, dict[str, Any]],
) -> tuple[list[datetime], np.ndarray, np.ndarray]:
    dates = sorted(decisions)
    rebalance = np.zeros(len(dates), dtype=bool)
    selected_rebalance = np.zeros(len(dates), dtype=bool)
    age = 3
    held: tuple[int, ...] = ()
    for position, stamp in enumerate(dates):
        decision = decisions[stamp]
        panic = decision["regime"] == 2
        due = age >= 3
        target = held
        if panic:
            target = ()
        elif due:
            target = tuple(int(value) for value in decision["selected"])
        changed = target != held
        if panic or due:
            rebalance[position] = True
            selected_rebalance[position] = bool(target) and due
            held = target
            if due or (panic and changed):
                age = 0
        age += 1
    return dates, rebalance, selected_rebalance


def build_fold_data(
    dataset: Dataset,
    cash_history: v44.CashRateHistory,
    fold: dict[str, Any],
) -> FoldData:
    spec = fold["spec"]
    panel_start = spec.validation_start - timedelta(days=230)
    panel_dates = sorted({
        stamp for stamp in dataset.dates
        if panel_start <= stamp <= spec.validation_end
    })
    panel_mask = v43.date_mask(dataset, panel_start, spec.validation_end)
    panel = market_panel(dataset, panel_dates)
    panel.update(model_panel(
        dataset,
        panel_dates,
        panel_mask,
        fold["bundle"],
        fold["predictions"],
    ))
    decisions = v43.decisions_by_date(
        dataset,
        fold["validation_mask"],
        fold["bundle"],
        fold["predictions"],
    )
    validation_dates, rebalance, selected_rebalance = baseline_schedule(decisions)
    positions = {stamp: index for index, stamp in enumerate(panel_dates)}
    validation_positions = np.asarray(
        [positions[stamp] for stamp in validation_dates], dtype=int
    )
    baseline_daily = np.asarray(
        fold["baseline"]["daily_returns"], dtype=float
    )
    if len(baseline_daily) != len(validation_dates):
        raise AdversarialAlphaFunnelV52Error(
            f"daily-return length mismatch in {spec.name}"
        )
    cash_daily = np.asarray([
        v44.annual_to_daily_rate(
            v44.prior_known_annual_rate(cash_history, stamp)[1]
        )
        for stamp in validation_dates
    ], dtype=float)
    risky_daily = baseline_daily - cash_daily
    return FoldData(
        name=spec.name,
        panel_dates=panel_dates,
        validation_dates=validation_dates,
        validation_positions=validation_positions,
        panel=panel,
        baseline=fold["baseline"],
        baseline_daily_returns=baseline_daily,
        risky_daily_returns=risky_daily,
        cash_daily_returns=cash_daily,
        rebalance_mask=rebalance,
        selected_rebalance_mask=selected_rebalance,
        bundle=fold["bundle"],
        predictions=fold["predictions"],
        validation_mask=fold["validation_mask"],
    )


def lagged(values: np.ndarray, lag: int) -> np.ndarray:
    output = np.full(len(values), math.nan, dtype=float)
    if lag < len(values):
        output[lag:] = values[:-lag]
    return output


def transformed_series(
    values: np.ndarray,
    transform: str,
    lag: int,
) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    previous = lagged(values, lag)
    if transform == "level":
        return values.copy()
    if transform == "delta":
        return values - previous
    if transform == "acceleration":
        twice_previous = lagged(values, 2 * lag)
        return values - 2.0 * previous + twice_previous
    if transform == "distance":
        output = np.full(len(values), math.nan, dtype=float)
        for index in range(lag, len(values)):
            history = values[index - lag:index]
            finite = history[np.isfinite(history)]
            if len(finite) == lag and math.isfinite(values[index]):
                output[index] = values[index] - float(np.median(finite))
        return output
    if transform == "local_vol":
        output = np.full(len(values), math.nan, dtype=float)
        width = max(3, lag)
        for index in range(width - 1, len(values)):
            sample = values[index - width + 1:index + 1]
            finite = sample[np.isfinite(sample)]
            if len(finite) == width:
                output[index] = float(np.std(finite))
        return output
    raise AdversarialAlphaFunnelV52Error(f"unknown transform: {transform}")


def rolling_percentile(values: np.ndarray, window: int) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    output = np.full(len(values), math.nan, dtype=float)
    if len(values) <= window:
        return output
    windows = np.lib.stride_tricks.sliding_window_view(
        values, window + 1
    )
    history = windows[:, :-1]
    current = windows[:, -1]
    valid = np.isfinite(current) & np.all(np.isfinite(history), axis=1)
    ranks = np.sum(history <= current[:, None], axis=1) / float(window)
    tail = output[window:]
    tail[valid] = ranks[valid]
    return output


def normalized_for_fold(
    fold: FoldData,
    hypothesis: Hypothesis,
    cache: dict[tuple[Any, ...], np.ndarray],
) -> np.ndarray:
    key = (
        fold.name,
        hypothesis.source,
        hypothesis.transform,
        hypothesis.history,
        hypothesis.lag,
    )
    if key not in cache:
        source = fold.panel[hypothesis.source]
        transformed = transformed_series(
            source, hypothesis.transform, hypothesis.lag
        )
        cache[key] = rolling_percentile(
            transformed, hypothesis.history
        ).astype(np.float32, copy=False)
    return cache[key]


def persist_events(events: np.ndarray, days: int) -> np.ndarray:
    active = np.zeros(len(events), dtype=bool)
    for index in np.flatnonzero(events):
        active[index:min(len(active), index + days)] = True
    return active


def activity_for_fold(
    fold: FoldData,
    hypothesis: Hypothesis,
    cache: dict[tuple[Any, ...], np.ndarray],
) -> np.ndarray:
    normalized = normalized_for_fold(fold, hypothesis, cache)
    finite = np.isfinite(normalized)
    threshold = hypothesis.threshold
    if hypothesis.event == "low":
        full = finite & (normalized <= threshold)
    elif hypothesis.event == "high":
        full = finite & (normalized >= threshold)
    else:
        previous = lagged(normalized, 1)
        pair_finite = finite & np.isfinite(previous)
        if hypothesis.event == "cross_up":
            events = pair_finite & (previous <= threshold) & (
                normalized > threshold
            )
        elif hypothesis.event == "cross_down":
            events = pair_finite & (previous >= threshold) & (
                normalized < threshold
            )
        else:
            raise AdversarialAlphaFunnelV52Error(
                f"unknown event: {hypothesis.event}"
            )
        full = persist_events(events, hypothesis.persistence)
    return full[fold.validation_positions]


def exposure_path(
    fold: FoldData,
    active: np.ndarray,
    multiplier: float,
) -> np.ndarray:
    exposure = np.ones(len(active), dtype=float)
    current = 1.0
    for index in range(len(active)):
        if fold.rebalance_mask[index]:
            if fold.selected_rebalance_mask[index]:
                current = multiplier if active[index] else 1.0
            else:
                current = 0.0
        exposure[index] = current
    return exposure


def compounded(values: list[float]) -> float:
    return float(np.prod([1.0 + value for value in values]) - 1.0)


def proxy_fold(
    fold: FoldData,
    active: np.ndarray,
    multiplier: float,
    *,
    penalty_scale: float = 1.0,
) -> dict[str, Any]:
    exposure = exposure_path(fold, active, multiplier)
    candidate_daily = fold.cash_daily_returns + (
        exposure * fold.risky_daily_returns
    )
    candidate_growth = float(np.prod(1.0 + candidate_daily))
    baseline_growth = float(np.prod(1.0 + fold.baseline_daily_returns))
    interventions = int(np.sum(active & fold.selected_rebalance_mask))
    penalty = (
        INTERVENTION_PENALTY
        * penalty_scale
        * (1.0 - multiplier)
        * interventions
    )
    excess = candidate_growth / max(baseline_growth, 1e-12) - 1.0 - penalty
    return {
        "fold": fold.name,
        "proxy_excess": float(excess),
        "interventions": interventions,
        "selected_rebalances": int(np.sum(fold.selected_rebalance_mask)),
        "active_days": int(np.sum(active)),
    }


def fingerprint_for(
    folds: list[FoldData],
    activities: list[np.ndarray],
    multiplier: float,
) -> str:
    digest = hashlib.sha256(f"{multiplier:.2f}".encode("ascii"))
    for fold, active in zip(folds, activities, strict=True):
        behavior = active & fold.selected_rebalance_mask
        digest.update(len(behavior).to_bytes(2, "little"))
        digest.update(np.packbits(behavior).tobytes())
    return digest.hexdigest()


def evaluate_proxy(
    folds: list[FoldData],
    hypothesis: Hypothesis,
    cache: dict[tuple[Any, ...], np.ndarray],
    *,
    penalty_scale: float = 1.0,
    activity_shift: int = 0,
    dropout_stride: int | None = None,
) -> tuple[str, dict[str, Any]]:
    activities: list[np.ndarray] = []
    fold_reports: list[dict[str, Any]] = []
    for fold in folds:
        active = activity_for_fold(fold, hypothesis, cache)
        if activity_shift:
            shifted = np.zeros_like(active)
            if activity_shift < len(active):
                shifted[activity_shift:] = active[:-activity_shift]
            active = shifted
        if dropout_stride is not None and dropout_stride > 1:
            indexes = np.flatnonzero(active)
            active = active.copy()
            active[indexes[::dropout_stride]] = False
        activities.append(active)
        fold_reports.append(proxy_fold(
            fold,
            active,
            hypothesis.multiplier,
            penalty_scale=penalty_scale,
        ))
    excesses = [float(value["proxy_excess"]) for value in fold_reports]
    interventions = sum(int(value["interventions"]) for value in fold_reports)
    selected = sum(int(value["selected_rebalances"]) for value in fold_reports)
    positive = sum(value > 0.0 for value in excesses)
    compound = compounded(excesses)
    positive_values = [max(value, 0.0) for value in excesses]
    concentration = (
        max(positive_values) / max(sum(positive_values), 1e-12)
        if positive_values else 1.0
    )
    report = {
        "minimum_fold_excess": min(excesses),
        "positive_fold_count": positive,
        "compounded_excess": compound,
        "intervention_count": interventions,
        "selected_rebalance_count": selected,
        "intervention_coverage": interventions / max(selected, 1),
        "best_fold_concentration": concentration,
        "fold_names": [value["fold"] for value in fold_reports],
        "fold_excesses": excesses,
        "fold_interventions": [
            int(value["interventions"]) for value in fold_reports
        ],
    }
    return fingerprint_for(folds, activities, hypothesis.multiplier), report


def source_inventory(panel: dict[str, np.ndarray]) -> dict[str, list[str]]:
    families = {
        "trend_state": [],
        "breadth_leadership": [],
        "positioning_flow": [],
        "volatility_structure": [],
        "model_confidence": [],
        "relative_reversal": sorted(panel),
    }
    for source in sorted(panel):
        family = source_family(source)
        if family in families:
            families[family].append(source)
    for family, sources in families.items():
        if not sources:
            raise AdversarialAlphaFunnelV52Error(
                f"empty source family: {family}"
            )
    return families


def generate_hypotheses(
    families: dict[str, list[str]],
) -> list[Hypothesis]:
    rng = random.Random(SEED)
    family_names = tuple(families)
    unique: dict[str, Hypothesis] = {}
    cursor = 0
    while len(unique) < RAW_HYPOTHESIS_COUNT:
        family = family_names[cursor % len(family_names)]
        cursor += 1
        source = rng.choice(families[family])
        if family == "relative_reversal":
            transform = rng.choice(("delta", "acceleration", "distance"))
            event = rng.choice(("cross_up", "cross_down"))
        else:
            transform = rng.choice(TRANSFORMS)
            event = rng.choice(EVENTS)
        persistence = (
            rng.choice(PERSISTENCE_DAYS)
            if event.startswith("cross_") else 1
        )
        hypothesis = Hypothesis(
            family=family,
            source=source,
            transform=transform,
            history=rng.choice(HISTORY_WINDOWS),
            lag=rng.choice(LAGS),
            event=event,
            threshold=rng.choice(THRESHOLDS),
            persistence=persistence,
            multiplier=rng.choice(MULTIPLIERS),
        )
        unique.setdefault(canonical_hypothesis(hypothesis), hypothesis)
    return list(unique.values())


def proxy_selection_key(candidate: Candidate) -> tuple[Any, ...]:
    report = candidate.proxy
    return (
        float(report["minimum_fold_excess"]),
        int(report["positive_fold_count"]),
        float(report["compounded_excess"]),
        -float(report["best_fold_concentration"]),
        -abs(float(report["intervention_coverage"]) - 0.20),
        -hypothesis_complexity(candidate.hypothesis)[0],
        canonical_hypothesis(candidate.hypothesis),
    )


def proxy_eligible(report: dict[str, Any]) -> bool:
    return bool(
        int(report["intervention_count"]) >= 6
        and 0.02 <= float(report["intervention_coverage"]) <= 0.55
        and int(report["positive_fold_count"]) >= 4
        and float(report["compounded_excess"]) > 0.0
        and float(report["minimum_fold_excess"]) >= -0.0030
        and float(report["best_fold_concentration"]) <= 0.75
    )


def stage_proxy_search(
    folds: list[FoldData],
    hypotheses: list[Hypothesis],
    cache: dict[tuple[Any, ...], np.ndarray],
) -> tuple[list[Candidate], dict[str, Any]]:
    seen_behaviors: set[str] = set()
    eligible_by_behavior: dict[str, Candidate] = {}
    structural_rejections = 0
    duplicate_behaviors = 0
    proxy_rejections = 0
    for index, hypothesis in enumerate(hypotheses, start=1):
        fingerprint, report = evaluate_proxy(folds, hypothesis, cache)
        duplicate = fingerprint in seen_behaviors
        if duplicate:
            duplicate_behaviors += 1
        else:
            seen_behaviors.add(fingerprint)
        if index % 10_000 == 0:
            print(json.dumps({
                "stage": "proxy",
                "processed": index,
                "unique_behaviors": len(seen_behaviors),
                "eligible_behaviors": len(eligible_by_behavior),
            }, sort_keys=True), flush=True)
        coverage = float(report["intervention_coverage"])
        if report["intervention_count"] == 0 or coverage > 0.90:
            structural_rejections += 1
            continue
        if not proxy_eligible(report):
            proxy_rejections += 1
            continue
        candidate = Candidate(hypothesis, fingerprint, report)
        existing = eligible_by_behavior.get(fingerprint)
        if existing is None or hypothesis_complexity(
            hypothesis
        ) < hypothesis_complexity(existing.hypothesis):
            eligible_by_behavior[fingerprint] = candidate

    eligible = list(eligible_by_behavior.values())
    eligible.sort(key=proxy_selection_key, reverse=True)
    retained = eligible[:PROXY_KEEP]
    family_counts: dict[str, int] = {}
    for candidate in retained:
        family_counts[candidate.hypothesis.family] = (
            family_counts.get(candidate.hypothesis.family, 0) + 1
        )
    report = {
        "raw_hypothesis_count": len(hypotheses),
        "structural_rejection_count": structural_rejections,
        "behavioral_duplicate_count": duplicate_behaviors,
        "unique_behavior_count": len(seen_behaviors),
        "proxy_rejection_count": proxy_rejections,
        "proxy_eligible_count": len(eligible),
        "proxy_retained_count": len(retained),
        "retained_family_counts": family_counts,
    }
    return retained, report


def clone_hypothesis(
    value: Hypothesis,
    **changes: Any,
) -> Hypothesis:
    payload = asdict(value)
    payload.update(changes)
    return Hypothesis(**payload)


def nearest_history(value: int, direction: int) -> int:
    index = HISTORY_WINDOWS.index(value)
    return HISTORY_WINDOWS[
        min(max(index + direction, 0), len(HISTORY_WINDOWS) - 1)
    ]


def nearest_threshold(value: float, direction: int) -> float:
    index = THRESHOLDS.index(value)
    return THRESHOLDS[
        min(max(index + direction, 0), len(THRESHOLDS) - 1)
    ]


def proxy_from_activities(
    folds: list[FoldData],
    activities: list[np.ndarray],
    multiplier: float,
    *,
    penalty_scale: float = 1.0,
) -> dict[str, Any]:
    reports = [
        proxy_fold(
            fold, active, multiplier, penalty_scale=penalty_scale
        )
        for fold, active in zip(folds, activities, strict=True)
    ]
    excesses = [float(value["proxy_excess"]) for value in reports]
    interventions = sum(int(value["interventions"]) for value in reports)
    selected = sum(int(value["selected_rebalances"]) for value in reports)
    positive_values = [max(value, 0.0) for value in excesses]
    return {
        "minimum_fold_excess": min(excesses),
        "positive_fold_count": sum(value > 0.0 for value in excesses),
        "compounded_excess": compounded(excesses),
        "intervention_count": interventions,
        "selected_rebalance_count": selected,
        "intervention_coverage": interventions / max(selected, 1),
        "best_fold_concentration": (
            max(positive_values) / max(sum(positive_values), 1e-12)
        ),
        "folds": reports,
    }


def candidate_activities(
    folds: list[FoldData],
    hypothesis: Hypothesis,
    cache: dict[tuple[Any, ...], np.ndarray],
) -> list[np.ndarray]:
    return [activity_for_fold(fold, hypothesis, cache) for fold in folds]


def circular_placebos(
    folds: list[FoldData],
    activities: list[np.ndarray],
    multiplier: float,
) -> list[float]:
    values: list[float] = []
    for offset in (7, 13, 19, 29, 37, 47, 61, 73):
        shifted = [
            np.roll(active, offset % max(len(active), 1))
            for active in activities
        ]
        values.append(float(proxy_from_activities(
            folds, shifted, multiplier
        )["compounded_excess"]))
    return values


def attack_candidate(
    folds: list[FoldData],
    candidate: Candidate,
    cache: dict[tuple[Any, ...], np.ndarray],
) -> dict[str, Any]:
    hypothesis = candidate.hypothesis
    _, delay1 = evaluate_proxy(
        folds, hypothesis, cache, activity_shift=1
    )
    _, delay2 = evaluate_proxy(
        folds, hypothesis, cache, activity_shift=2
    )
    low_threshold = clone_hypothesis(
        hypothesis,
        threshold=nearest_threshold(hypothesis.threshold, -1),
    )
    high_threshold = clone_hypothesis(
        hypothesis,
        threshold=nearest_threshold(hypothesis.threshold, 1),
    )
    _, threshold_low = evaluate_proxy(folds, low_threshold, cache)
    _, threshold_high = evaluate_proxy(folds, high_threshold, cache)
    short_history = clone_hypothesis(
        hypothesis,
        history=nearest_history(hypothesis.history, -1),
    )
    long_history = clone_hypothesis(
        hypothesis,
        history=nearest_history(hypothesis.history, 1),
    )
    _, history_short = evaluate_proxy(folds, short_history, cache)
    _, history_long = evaluate_proxy(folds, long_history, cache)
    _, dropout = evaluate_proxy(
        folds, hypothesis, cache, dropout_stride=10
    )
    _, doubled_penalty = evaluate_proxy(
        folds, hypothesis, cache, penalty_scale=2.0
    )
    original_excess = [
        float(value) for value in candidate.proxy["fold_excesses"]
    ]
    best_index = int(np.argmax(original_excess))
    without_best = compounded([
        value for index, value in enumerate(original_excess)
        if index != best_index
    ])
    activities = candidate_activities(folds, hypothesis, cache)
    placebos = circular_placebos(
        folds, activities, hypothesis.multiplier
    )
    placebo_percentile = float(np.mean(
        float(candidate.proxy["compounded_excess"]) > np.asarray(placebos)
    ))
    reports = {
        "delay_1": delay1,
        "delay_2": delay2,
        "threshold_lower": threshold_low,
        "threshold_higher": threshold_high,
        "history_shorter": history_short,
        "history_longer": history_long,
        "dropout_every_tenth_active": dropout,
        "doubled_intervention_penalty": doubled_penalty,
        "without_best_fold_compounded_excess": without_best,
        "circular_placebo_compounded_excess": placebos,
        "placebo_percentile": placebo_percentile,
    }
    reports["eligible"] = bool(
        float(delay1["compounded_excess"]) >= -0.0015
        and float(delay2["compounded_excess"]) >= -0.0025
        and min(
            float(threshold_low["compounded_excess"]),
            float(threshold_high["compounded_excess"]),
        ) >= -0.0025
        and min(
            float(history_short["compounded_excess"]),
            float(history_long["compounded_excess"]),
        ) >= -0.0030
        and max(
            float(history_short["compounded_excess"]),
            float(history_long["compounded_excess"]),
        ) > 0.0
        and float(dropout["compounded_excess"]) >= -0.0010
        and float(doubled_penalty["compounded_excess"]) > 0.0
        and without_best > 0.0
        and placebo_percentile >= 0.75
    )
    return reports


def attack_selection_key(candidate: Candidate) -> tuple[Any, ...]:
    assert candidate.attacks is not None
    attack = candidate.attacks
    neighbor_floor = min(
        float(attack[name]["compounded_excess"])
        for name in (
            "delay_1", "delay_2", "threshold_lower",
            "threshold_higher", "history_shorter", "history_longer",
        )
    )
    return (
        neighbor_floor,
        float(attack["without_best_fold_compounded_excess"]),
        float(attack["doubled_intervention_penalty"]["compounded_excess"]),
        float(candidate.proxy["minimum_fold_excess"]),
        float(candidate.proxy["compounded_excess"]),
        canonical_hypothesis(candidate.hypothesis),
    )


def stage_attacks(
    folds: list[FoldData],
    candidates: list[Candidate],
    cache: dict[tuple[Any, ...], np.ndarray],
) -> tuple[list[Candidate], dict[str, Any]]:
    attacked = candidates[:ATTACK_KEEP]
    survivors: list[Candidate] = []
    for index, candidate in enumerate(attacked, start=1):
        candidate.attacks = attack_candidate(folds, candidate, cache)
        if candidate.attacks["eligible"]:
            survivors.append(candidate)
        if index % 64 == 0:
            print(json.dumps({
                "stage": "attacks",
                "processed": index,
                "survivors": len(survivors),
            }, sort_keys=True), flush=True)
    survivors.sort(key=attack_selection_key, reverse=True)
    return survivors, {
        "attacked_count": len(attacked),
        "attack_survivor_count": len(survivors),
        "exact_retained_count": min(len(survivors), EXACT_KEEP),
    }


def probabilities_from_activity(
    fold: FoldData,
    active: np.ndarray,
) -> dict[datetime, float]:
    return {
        stamp: (0.0 if bool(active[index]) else 1.0)
        for index, stamp in enumerate(fold.validation_dates)
    }


def exact_fold(
    dataset: Dataset,
    cash_history: v44.CashRateHistory,
    fold: FoldData,
    active: np.ndarray,
    multiplier: float,
    one_way_cost: float,
) -> dict[str, Any]:
    result = v48.simulate_attenuation(
        dataset,
        fold.validation_mask,
        fold.bundle,
        fold.predictions,
        cash_history,
        probabilities_from_activity(fold, active),
        0.5,
        multiplier,
        one_way_cost=one_way_cost,
    )
    result["baseline_net_return"] = float(fold.baseline["net_return"])
    result["excess_return"] = (
        float(result["net_return"])
        - float(fold.baseline["net_return"])
    )
    return result


def relative_compound(
    candidate_returns: list[float],
    baseline_returns: list[float],
) -> float:
    candidate_growth = float(np.prod([
        1.0 + value for value in candidate_returns
    ]))
    baseline_growth = float(np.prod([
        1.0 + value for value in baseline_returns
    ]))
    return candidate_growth / max(baseline_growth, 1e-12) - 1.0


def exact_candidate(
    dataset: Dataset,
    cash_history: v44.CashRateHistory,
    folds: list[FoldData],
    candidate: Candidate,
    cache: dict[tuple[Any, ...], np.ndarray],
) -> dict[str, Any]:
    activities = candidate_activities(
        folds, candidate.hypothesis, cache
    )
    standard: list[dict[str, Any]] = []
    stress: list[dict[str, Any]] = []
    stress_baselines: list[dict[str, Any]] = []
    for fold, active in zip(folds, activities, strict=True):
        standard.append(exact_fold(
            dataset,
            cash_history,
            fold,
            active,
            candidate.hypothesis.multiplier,
            STANDARD_ONE_WAY_COST,
        ))
        baseline_stress = v44.simulate(
            dataset,
            fold.validation_mask,
            fold.bundle,
            fold.predictions,
            cash_history,
            one_way_cost=STRESS_ONE_WAY_COST,
        )
        stress_result = exact_fold(
            dataset,
            cash_history,
            fold,
            active,
            candidate.hypothesis.multiplier,
            STRESS_ONE_WAY_COST,
        )
        stress_result["baseline_net_return"] = float(
            baseline_stress["net_return"]
        )
        stress_result["excess_return"] = (
            float(stress_result["net_return"])
            - float(baseline_stress["net_return"])
        )
        stress_baselines.append(baseline_stress)
        stress.append(stress_result)
    standard_returns = [float(value["net_return"]) for value in standard]
    standard_baseline_returns = [
        float(fold.baseline["net_return"]) for fold in folds
    ]
    stress_returns = [float(value["net_return"]) for value in stress]
    stress_baseline_returns = [
        float(value["net_return"]) for value in stress_baselines
    ]
    standard_excess = [
        float(value["excess_return"]) for value in standard
    ]
    stress_excess = [float(value["excess_return"]) for value in stress]
    report = {
        "standard_folds": standard,
        "stress_folds": stress,
        "standard_compounded_excess": relative_compound(
            standard_returns, standard_baseline_returns
        ),
        "stress_compounded_excess": relative_compound(
            stress_returns, stress_baseline_returns
        ),
        "minimum_standard_fold_excess": min(standard_excess),
        "minimum_stress_fold_excess": min(stress_excess),
        "positive_standard_fold_count": sum(
            value > 0.0 for value in standard_excess
        ),
        "positive_stress_fold_count": sum(
            value > 0.0 for value in stress_excess
        ),
        "attenuated_decision_count": sum(
            int(value["attenuated_decision_count"]) for value in standard
        ),
        "actions_not_increased": all(
            int(value["target_changing_actions"])
            <= int(fold.baseline["target_changing_actions"])
            for value, fold in zip(standard, folds, strict=True)
        ),
        "never_added_asset": all(
            bool(value["never_added_asset"]) for value in standard + stress
        ),
        "never_increased_target": all(
            bool(value["never_increased_target"]) for value in standard + stress
        ),
    }
    report["eligible"] = bool(
        report["positive_standard_fold_count"] >= 4
        and report["standard_compounded_excess"] > 0.0
        and report["minimum_standard_fold_excess"] >= -0.0025
        and report["stress_compounded_excess"] > 0.0
        and report["minimum_stress_fold_excess"] >= -0.0025
        and report["attenuated_decision_count"] >= 6
        and report["actions_not_increased"]
        and report["never_added_asset"]
        and report["never_increased_target"]
    )
    return report


def exact_selection_key(candidate: Candidate) -> tuple[Any, ...]:
    assert candidate.exact is not None
    return (
        float(candidate.exact["minimum_standard_fold_excess"]),
        int(candidate.exact["positive_standard_fold_count"]),
        float(candidate.exact["stress_compounded_excess"]),
        float(candidate.exact["standard_compounded_excess"]),
        -int(candidate.exact["attenuated_decision_count"]),
        canonical_hypothesis(candidate.hypothesis),
    )


def stage_exact(
    dataset: Dataset,
    cash_history: v44.CashRateHistory,
    folds: list[FoldData],
    candidates: list[Candidate],
    cache: dict[tuple[Any, ...], np.ndarray],
) -> tuple[list[Candidate], dict[str, Any]]:
    tested = candidates[:EXACT_KEEP]
    survivors: list[Candidate] = []
    for index, candidate in enumerate(tested, start=1):
        candidate.exact = exact_candidate(
            dataset, cash_history, folds, candidate, cache
        )
        if candidate.exact["eligible"]:
            survivors.append(candidate)
        if index % 8 == 0:
            print(json.dumps({
                "stage": "exact",
                "processed": index,
                "survivors": len(survivors),
            }, sort_keys=True), flush=True)
    survivors.sort(key=exact_selection_key, reverse=True)
    return survivors, {
        "exact_tested_count": len(tested),
        "exact_survivor_count": len(survivors),
        "deep_attack_retained_count": min(
            len(survivors), DEEP_ATTACK_KEEP
        ),
    }


def exact_standard_variant(
    dataset: Dataset,
    cash_history: v44.CashRateHistory,
    folds: list[FoldData],
    hypothesis: Hypothesis,
    cache: dict[tuple[Any, ...], np.ndarray],
    *,
    activity_shift: int = 0,
) -> dict[str, Any]:
    activities = candidate_activities(folds, hypothesis, cache)
    if activity_shift:
        shifted_activities: list[np.ndarray] = []
        for active in activities:
            shifted = np.zeros_like(active)
            if activity_shift < len(active):
                shifted[activity_shift:] = active[:-activity_shift]
            shifted_activities.append(shifted)
        activities = shifted_activities
    results = [
        exact_fold(
            dataset,
            cash_history,
            fold,
            active,
            hypothesis.multiplier,
            STANDARD_ONE_WAY_COST,
        )
        for fold, active in zip(folds, activities, strict=True)
    ]
    returns = [float(value["net_return"]) for value in results]
    baseline_returns = [
        float(fold.baseline["net_return"]) for fold in folds
    ]
    excess = [float(value["excess_return"]) for value in results]
    return {
        "folds": results,
        "compounded_excess": relative_compound(
            returns, baseline_returns
        ),
        "minimum_fold_excess": min(excess),
        "positive_fold_count": sum(value > 0.0 for value in excess),
        "attenuated_decision_count": sum(
            int(value["attenuated_decision_count"]) for value in results
        ),
    }


def deep_attack_candidate(
    dataset: Dataset,
    cash_history: v44.CashRateHistory,
    folds: list[FoldData],
    candidate: Candidate,
    cache: dict[tuple[Any, ...], np.ndarray],
) -> dict[str, Any]:
    hypothesis = candidate.hypothesis
    variants = {
        "delay_1": (
            hypothesis,
            1,
        ),
        "threshold_lower": (
            clone_hypothesis(
                hypothesis,
                threshold=nearest_threshold(hypothesis.threshold, -1),
            ),
            0,
        ),
        "threshold_higher": (
            clone_hypothesis(
                hypothesis,
                threshold=nearest_threshold(hypothesis.threshold, 1),
            ),
            0,
        ),
        "history_shorter": (
            clone_hypothesis(
                hypothesis,
                history=nearest_history(hypothesis.history, -1),
            ),
            0,
        ),
        "history_longer": (
            clone_hypothesis(
                hypothesis,
                history=nearest_history(hypothesis.history, 1),
            ),
            0,
        ),
    }
    reports = {
        name: exact_standard_variant(
            dataset,
            cash_history,
            folds,
            variant,
            cache,
            activity_shift=shift,
        )
        for name, (variant, shift) in variants.items()
    }
    threshold_floor = min(
        float(reports["threshold_lower"]["compounded_excess"]),
        float(reports["threshold_higher"]["compounded_excess"]),
    )
    history_floor = min(
        float(reports["history_shorter"]["compounded_excess"]),
        float(reports["history_longer"]["compounded_excess"]),
    )
    reports["eligible"] = bool(
        float(reports["delay_1"]["compounded_excess"]) >= -0.0010
        and threshold_floor >= -0.0025
        and max(
            float(reports["threshold_lower"]["compounded_excess"]),
            float(reports["threshold_higher"]["compounded_excess"]),
        ) > 0.0
        and history_floor >= -0.0030
        and max(
            float(reports["history_shorter"]["compounded_excess"]),
            float(reports["history_longer"]["compounded_excess"]),
        ) > 0.0
        and min(
            int(reports[name]["positive_fold_count"])
            for name in reports if isinstance(reports[name], dict)
        ) >= 3
    )
    return reports


def stage_deep_attacks(
    dataset: Dataset,
    cash_history: v44.CashRateHistory,
    folds: list[FoldData],
    candidates: list[Candidate],
    cache: dict[tuple[Any, ...], np.ndarray],
) -> tuple[list[Candidate], dict[str, Any]]:
    tested = candidates[:DEEP_ATTACK_KEEP]
    survivors: list[Candidate] = []
    for candidate in tested:
        candidate.deep_attacks = deep_attack_candidate(
            dataset, cash_history, folds, candidate, cache
        )
        if candidate.deep_attacks["eligible"]:
            survivors.append(candidate)
    survivors.sort(key=exact_selection_key, reverse=True)
    return survivors, {
        "deep_attack_tested_count": len(tested),
        "deep_attack_survivor_count": len(survivors),
    }


def candidate_payload(candidate: Candidate) -> dict[str, Any]:
    return {
        "hypothesis": asdict(candidate.hypothesis),
        "fingerprint": candidate.fingerprint,
        "proxy": candidate.proxy,
        "attacks": candidate.attacks,
        "exact": candidate.exact,
        "deep_attacks": candidate.deep_attacks,
    }


def choose_shortlist(candidates: list[Candidate]) -> list[Candidate]:
    selected: list[Candidate] = []
    used_families: set[str] = set()
    for candidate in candidates:
        family = candidate.hypothesis.family
        if family in used_families:
            continue
        selected.append(candidate)
        used_families.add(family)
        if len(selected) >= MAX_SHORTLIST:
            break
    return selected


def runtime_versions() -> dict[str, str]:
    return v48.runtime_versions()


def manifest_bytes(hypotheses: list[Hypothesis]) -> bytes:
    return (
        "\n".join(canonical_hypothesis(value) for value in hypotheses)
        + "\n"
    ).encode("utf-8")


def run_campaign(
    baseline_report: dict[str, Any],
    bundle: v43.Bundle,
    *,
    baseline_bundle_sha256: str | None = None,
    manifest_path: Path | None = None,
    states: dict[str, Any] | None = None,
    source_report: dict[str, Any] | None = None,
    cash_history: v44.CashRateHistory | None = None,
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
        raise AdversarialAlphaFunnelV52Error("source report unavailable")
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
    raw_folds = v46.build_walk_forward_folds(dataset, cash_history)
    folds = [
        build_fold_data(dataset, cash_history, fold)
        for fold in raw_folds
    ]
    first_sources = set(folds[0].panel)
    if any(set(fold.panel) != first_sources for fold in folds[1:]):
        raise AdversarialAlphaFunnelV52Error(
            "fold source inventories are inconsistent"
        )
    families = source_inventory(folds[0].panel)
    hypotheses = generate_hypotheses(families)
    manifest = manifest_bytes(hypotheses)
    manifest_sha256 = hashlib.sha256(manifest).hexdigest()
    if manifest_path is not None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_bytes(manifest)
    cache: dict[tuple[Any, ...], np.ndarray] = {}
    proxy_candidates, proxy_report = stage_proxy_search(
        folds, hypotheses, cache
    )
    attack_candidates, attack_report = stage_attacks(
        folds, proxy_candidates, cache
    )
    exact_candidates, exact_report = stage_exact(
        dataset,
        cash_history,
        folds,
        attack_candidates,
        cache,
    )
    deep_candidates, deep_report = stage_deep_attacks(
        dataset,
        cash_history,
        folds,
        exact_candidates,
        cache,
    )
    shortlist = choose_shortlist(deep_candidates)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_iso(datetime.now(timezone.utc)),
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": True,
        "retrospective": True,
        "untouched_historical_dates": False,
        "sealed_evaluation_performed": False,
        "universe": list(ASSETS),
        "source": source_report,
        "cash_source": cash_history.source,
        "runtime": runtime_versions(),
        "protocol_sha256": file_sha256(PROTOCOL_PATH),
        "implementation_contract_sha256": file_sha256(CONTRACT_PATH),
        "implementation_sha256": file_sha256(Path(__file__).resolve()),
        "baseline_report_sha256": baseline_report["report_sha256"],
        "baseline_bundle_sha256": baseline_bundle_sha256,
        "v44_reproduction_report_sha256": reproduction["report_sha256"],
        "reproduction": {
            **reproduction["reproduction"],
            "walk_forward_fold_count": len(folds),
            "sealed_evaluation_performed": False,
            "final_v43_retrained_for_v52": False,
        },
        "search_contract": {
            "seed": SEED,
            "raw_hypothesis_count": RAW_HYPOTHESIS_COUNT,
            "history_windows": list(HISTORY_WINDOWS),
            "lags": list(LAGS),
            "thresholds": list(THRESHOLDS),
            "events": list(EVENTS),
            "persistence_days": list(PERSISTENCE_DAYS),
            "multipliers": list(MULTIPLIERS),
            "transforms": list(TRANSFORMS),
            "proxy_keep": PROXY_KEEP,
            "attack_keep": ATTACK_KEEP,
            "exact_keep": EXACT_KEEP,
            "deep_attack_keep": DEEP_ATTACK_KEEP,
            "maximum_shortlist": MAX_SHORTLIST,
        },
        "source_inventory": {
            "source_count": len(first_sources),
            "sources": sorted(first_sources),
            "families": families,
        },
        "hypothesis_manifest": {
            "count": len(hypotheses),
            "sha256": manifest_sha256,
            "path": str(manifest_path) if manifest_path else None,
            "first": asdict(hypotheses[0]),
            "last": asdict(hypotheses[-1]),
        },
        "funnel": {
            "proxy": proxy_report,
            "attacks": attack_report,
            "exact": exact_report,
            "deep_attacks": deep_report,
        },
        "top_proxy_candidates": [
            candidate_payload(value) for value in proxy_candidates[:20]
        ],
        "top_attack_survivors": [
            candidate_payload(value) for value in attack_candidates[:20]
        ],
        "top_exact_survivors": [
            candidate_payload(value) for value in exact_candidates[:20]
        ],
        "top_deep_attack_survivors": [
            candidate_payload(value) for value in deep_candidates[:20]
        ],
        "shortlist": [candidate_payload(value) for value in shortlist],
        "shortlist_count": len(shortlist),
        "status": (
            "RETROSPECTIVE_SHORTLIST_READY_FOR_FREEZE"
            if shortlist else "RETROSPECTIVE_NO_ROBUST_SURVIVOR"
        ),
        "accepted_strategy_remains": "v4.4-yield-bearing-cash",
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run v5.2 adversarial alpha discovery funnel"
    )
    parser.add_argument("--baseline-json", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("evidence/v52/historical.json"),
    )
    parser.add_argument(
        "--manifest-out",
        type=Path,
        default=Path("evidence/v52/hypotheses.jsonl"),
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
        manifest_path=args.manifest_out,
        monthly_workers=max(1, args.monthly_workers),
        metrics_workers=max(1, args.metrics_workers),
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": report["status"],
        "raw_hypothesis_count": report["hypothesis_manifest"]["count"],
        "unique_behavior_count": report["funnel"]["proxy"][
            "unique_behavior_count"
        ],
        "proxy_survivors": report["funnel"]["proxy"][
            "proxy_retained_count"
        ],
        "attack_survivors": report["funnel"]["attacks"][
            "attack_survivor_count"
        ],
        "exact_survivors": report["funnel"]["exact"][
            "exact_survivor_count"
        ],
        "deep_attack_survivors": report["funnel"]["deep_attacks"][
            "deep_attack_survivor_count"
        ],
        "shortlist_count": report["shortlist_count"],
        "shortlist": [
            value["hypothesis"] for value in report["shortlist"]
        ],
        "report_sha256": report["report_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
