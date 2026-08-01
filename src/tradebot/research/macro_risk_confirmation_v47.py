from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import io
import itertools
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np

from tradebot.research import distributional_utility_v43 as v43
from tradebot.research import regime_diversified_utility_v45 as v45
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

SCHEMA_VERSION = "4.7-macro-risk-confirmation"
PROTOCOL_PATH = Path("research/V47_MACRO_RISK_CONFIRMATION_PROTOCOL.md")
CONTRACT_PATH = Path(
    "research/V471_MACRO_RISK_CONFIRMATION_IMPLEMENTATION_CONTRACT.md"
)
MACRO_START = "2020-01-01"
MACRO_END = "2026-06-30"
MACRO_SERIES = ("VIXCLS", "DTWEXBGS", "DFII10")
MACRO_URLS = {
    series: (
        "https://fred.stlouisfed.org/graph/fredgraph.csv?"
        f"id={series}&cosd={MACRO_START}&coed={MACRO_END}"
    )
    for series in MACRO_SERIES
}
MACRO_PROVIDER = "fred-federal-reserve-public-csv"
MIN_MACRO_OBSERVATIONS = 1_000
MAX_STALE_DAYS = 7


class MacroRiskConfirmationV47Error(RuntimeError):
    pass


@dataclass(frozen=True)
class MacroHistory:
    values: dict[str, dict[datetime, float]]
    source: dict[str, Any]

    def dates(self, series: str) -> tuple[datetime, ...]:
        return tuple(sorted(self.values[series]))


@dataclass(frozen=True)
class MacroSnapshot:
    score: float
    components: dict[str, float]
    asof_dates: dict[str, datetime]
    raw_values: dict[str, float]


@dataclass(frozen=True)
class MacroConfig:
    supportive_threshold: float
    defensive_threshold: float
    supportive_multiplier: float
    defensive_multiplier: float

    @property
    def disabled(self) -> bool:
        return (
            self.supportive_multiplier == 1.0
            and self.defensive_multiplier == 1.0
        )


DISABLED_MACRO = MacroConfig(0.0, 1.0, 1.0, 1.0)


def parse_fred_series(content: bytes, series: str) -> dict[datetime, float]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MacroRiskConfirmationV47Error(
            f"{series} CSV is not UTF-8"
        ) from exc
    reader = csv.DictReader(io.StringIO(text))
    fields = list(reader.fieldnames or [])
    date_column = (
        "observation_date"
        if "observation_date" in fields
        else "DATE"
        if "DATE" in fields
        else None
    )
    if date_column is None or series not in fields:
        raise MacroRiskConfirmationV47Error(
            f"{series} CSV columns unavailable: {fields}"
        )
    result: dict[datetime, float] = {}
    for row in reader:
        raw = str(row.get(series, "")).strip()
        if not raw or raw == ".":
            continue
        try:
            stamp = datetime.fromisoformat(
                str(row[date_column]).strip()
            ).replace(tzinfo=timezone.utc)
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise MacroRiskConfirmationV47Error(
                f"invalid {series} observation: {row}"
            ) from exc
        if not np.isfinite(value):
            raise MacroRiskConfirmationV47Error(
                f"non-finite {series} observation on {stamp.date()}"
            )
        if stamp in result:
            raise MacroRiskConfirmationV47Error(
                f"duplicate {series} observation on {stamp.date()}"
            )
        result[stamp] = value
    if not result:
        raise MacroRiskConfirmationV47Error(
            f"{series} CSV has no usable observations"
        )
    return result


def _download_series(
    series: str,
    *,
    timeout: float = 30.0,
) -> tuple[dict[datetime, float], dict[str, Any]]:
    url = MACRO_URLS[series]
    request = Request(
        url,
        headers={
            "User-Agent": "tradebot-v47-macro-risk-confirmation/1.0",
            "Accept": "text/csv,*/*;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            if response.status != 200:
                raise MacroRiskConfirmationV47Error(
                    f"{series} returned HTTP {response.status}"
                )
            raw = response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise MacroRiskConfirmationV47Error(
            f"{series} download failed: {exc}"
        ) from exc
    if not raw:
        raise MacroRiskConfirmationV47Error(
            f"{series} source returned an empty response"
        )
    values = parse_fred_series(raw, series)
    if len(values) < MIN_MACRO_OBSERVATIONS:
        raise MacroRiskConfirmationV47Error(
            f"{series} has only {len(values)} observations; "
            f"minimum is {MIN_MACRO_OBSERVATIONS}"
        )
    dates = sorted(values)
    return values, {
        "provider": MACRO_PROVIDER,
        "series": series,
        "url": url,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "observation_count": len(values),
        "first_date": dates[0].date().isoformat(),
        "last_date": dates[-1].date().isoformat(),
    }


def load_macro_history(*, timeout: float = 30.0) -> MacroHistory:
    values: dict[str, dict[datetime, float]] = {}
    inventory: list[dict[str, Any]] = []
    for series in MACRO_SERIES:
        series_values, source = _download_series(
            series,
            timeout=timeout,
        )
        values[series] = series_values
        inventory.append(source)
    return MacroHistory(
        values=values,
        source={
            "schema_version": SCHEMA_VERSION + "-source",
            "provider": MACRO_PROVIDER,
            "source_start": MACRO_START,
            "source_end": MACRO_END,
            "series": inventory,
            "inventory_sha256": hashlib.sha256(
                canonical_json(inventory).encode("utf-8")
            ).hexdigest(),
        },
    )


def _asof_index(
    history: MacroHistory,
    series: str,
    stamp: datetime,
) -> tuple[tuple[datetime, ...], int]:
    cutoff = stamp - timedelta(days=1)
    dates = history.dates(series)
    index = bisect.bisect_right(dates, cutoff) - 1
    if index < 0:
        raise MacroRiskConfirmationV47Error(
            f"no prior-known {series} observation for {utc_iso(stamp)}"
        )
    observed = dates[index]
    if (cutoff.date() - observed.date()).days > MAX_STALE_DAYS:
        raise MacroRiskConfirmationV47Error(
            f"stale {series} observation for {utc_iso(stamp)}: "
            f"{observed.date()}"
        )
    return dates, index


def _midrank_percentile(values: list[float], current: float) -> float:
    if not values:
        raise MacroRiskConfirmationV47Error(
            "cannot calculate percentile from an empty sample"
        )
    less = sum(value < current for value in values)
    equal = sum(value == current for value in values)
    percentile = (less + 0.5 * equal) / len(values)
    return float(min(1.0, max(0.0, percentile)))


def _level_percentile(
    history: MacroHistory,
    series: str,
    stamp: datetime,
    *,
    window: int = 252,
    minimum: int = 126,
) -> tuple[float, datetime, float]:
    dates, index = _asof_index(history, series, stamp)
    start = max(0, index - window + 1)
    sample_dates = dates[start:index + 1]
    if len(sample_dates) < minimum:
        raise MacroRiskConfirmationV47Error(
            f"insufficient {series} level history for {utc_iso(stamp)}"
        )
    values = history.values[series]
    current = float(values[dates[index]])
    sample = [float(values[day]) for day in sample_dates]
    return (
        _midrank_percentile(sample, current),
        dates[index],
        current,
    )


def _change_percentile(
    history: MacroHistory,
    series: str,
    stamp: datetime,
    *,
    lookback: int,
    ratio: bool,
    window: int = 252,
    minimum: int = 126,
) -> tuple[float, datetime, float]:
    dates, index = _asof_index(history, series, stamp)
    if index < lookback:
        raise MacroRiskConfirmationV47Error(
            f"insufficient {series} lookback for {utc_iso(stamp)}"
        )
    values = history.values[series]

    def transformed(position: int) -> float:
        current = float(values[dates[position]])
        prior = float(values[dates[position - lookback]])
        if ratio:
            if prior == 0.0:
                raise MacroRiskConfirmationV47Error(
                    f"zero {series} denominator on {dates[position].date()}"
                )
            return current / prior - 1.0
        return current - prior

    first = max(lookback, index - window + 1)
    sample = [transformed(position) for position in range(first, index + 1)]
    if len(sample) < minimum:
        raise MacroRiskConfirmationV47Error(
            f"insufficient transformed {series} history for {utc_iso(stamp)}"
        )
    current = transformed(index)
    return (
        _midrank_percentile(sample, current),
        dates[index],
        current,
    )


def macro_snapshot(
    history: MacroHistory,
    stamp: datetime,
) -> MacroSnapshot:
    vix, vix_date, vix_value = _level_percentile(
        history,
        "VIXCLS",
        stamp,
    )
    dollar, dollar_date, dollar_value = _change_percentile(
        history,
        "DTWEXBGS",
        stamp,
        lookback=60,
        ratio=True,
    )
    real_yield, real_yield_date, real_yield_value = _change_percentile(
        history,
        "DFII10",
        stamp,
        lookback=20,
        ratio=False,
    )
    components = {
        "vix_level_percentile": vix,
        "dollar_return_60_percentile": dollar,
        "real_yield_change_20_percentile": real_yield,
    }
    return MacroSnapshot(
        score=float(np.mean(list(components.values()))),
        components=components,
        asof_dates={
            "VIXCLS": vix_date,
            "DTWEXBGS": dollar_date,
            "DFII10": real_yield_date,
        },
        raw_values={
            "VIXCLS": vix_value,
            "DTWEXBGS_return_60": dollar_value,
            "DFII10_change_20": real_yield_value,
        },
    )


def build_macro_snapshots(
    dates: list[datetime] | tuple[datetime, ...],
    history: MacroHistory,
) -> dict[datetime, MacroSnapshot]:
    return {
        stamp: macro_snapshot(history, stamp)
        for stamp in sorted(set(dates))
    }


def macro_grid() -> list[MacroConfig]:
    active = [
        MacroConfig(low, high, supportive, defensive)
        for low, high, supportive, defensive in itertools.product(
            (0.30, 0.40),
            (0.60, 0.70),
            (1.00, 1.25, 1.50),
            (0.00, 0.50, 0.75),
        )
    ]
    return [DISABLED_MACRO, *sorted(
        active,
        key=lambda value: (
            value.supportive_threshold,
            value.defensive_threshold,
            value.supportive_multiplier,
            value.defensive_multiplier,
        ),
    )]


def macro_state(score: float, config: MacroConfig) -> str:
    if config.disabled:
        return "neutral"
    if score <= config.supportive_threshold:
        return "supportive"
    if score >= config.defensive_threshold:
        return "defensive"
    return "neutral"


def target_multiplier(score: float, config: MacroConfig) -> float:
    state = macro_state(score, config)
    if state == "supportive":
        return config.supportive_multiplier
    if state == "defensive":
        return config.defensive_multiplier
    return 1.0


def simulate_macro(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    cash_history: v44.CashRateHistory,
    snapshots: dict[datetime, MacroSnapshot],
    config: MacroConfig,
    *,
    one_way_cost: float,
) -> dict[str, Any]:
    decisions = v43.decisions_by_date(
        dataset,
        mask,
        bundle,
        predictions,
    )
    index_map = {
        (dataset.dates[index], dataset.assets[index]): index
        for index in np.flatnonzero(mask)
    }
    cash = 1.0
    holdings = {asset: 0.0 for asset in ASSETS}
    holding_regime = {asset: 0 for asset in ASSETS}
    selected_assets: tuple[str, ...] = ()
    selected_regime = 0
    decision_selected_ever: set[str] = set()
    held_ever: set[str] = set()
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
    macro_state_counts = {
        "supportive": 0,
        "neutral": 0,
        "defensive": 0,
    }
    multiplier_counts: dict[str, int] = {}
    selected_identity_mismatches = 0

    for stamp in sorted(decisions):
        if stamp not in snapshots:
            raise MacroRiskConfirmationV47Error(
                f"macro snapshot unavailable for {utc_iso(stamp)}"
            )
        equity_before = cash + sum(holdings.values())
        decision = decisions[stamp]
        panic = decision["regime"] == 2
        due = age >= 3
        target_assets = selected_assets
        regime = selected_regime
        multiplier = 1.0
        state = "neutral"
        if panic:
            target_assets = ()
            regime = 2
            multiplier = 0.0
            state = "defensive"
        elif due:
            target_assets = tuple(
                dataset.assets[index]
                for index in decision["selected"]
            )
            regime = int(decision["regime"])
            decision_selected_ever.update(target_assets)
            snapshot = snapshots[stamp]
            state = macro_state(snapshot.score, config)
            multiplier = target_multiplier(snapshot.score, config)
            macro_state_counts[state] += 1
            multiplier_key = f"{multiplier:.2f}"
            multiplier_counts[multiplier_key] = (
                multiplier_counts.get(multiplier_key, 0) + 1
            )

        if panic or due:
            target_values = {
                asset: (
                    0.05 * multiplier * equity_before
                    if asset in target_assets
                    else 0.0
                )
                for asset in ASSETS
            }
            target_exposure = sum(target_values.values()) / max(
                equity_before,
                1e-12,
            )
            if target_exposure > 0.1500001:
                raise MacroRiskConfirmationV47Error(
                    f"macro target exposure exceeded 15%: {target_exposure}"
                )
            maximum_target_exposure = max(
                maximum_target_exposure,
                target_exposure,
            )
            expected_nonzero = {
                asset for asset in target_assets if multiplier > 0.0
            }
            observed_nonzero = {
                asset for asset, value in target_values.items()
                if value > 0.0
            }
            if observed_nonzero != expected_nonzero:
                selected_identity_mismatches += 1
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
            selected_regime = regime
            held_ever.update(observed_nonzero)
            for asset in observed_nonzero:
                holding_regime[asset] = regime
            if due or (panic and changed):
                age = 0

        equity_open = cash + sum(holdings.values())
        maximum_gross_exposure = max(
            maximum_gross_exposure,
            sum(holdings.values()) / max(equity_open, 1e-12),
        )
        _, annual_rate = v44.prior_known_annual_rate(
            cash_history,
            stamp,
        )
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
        "selected_assets": sorted(held_ever),
        "decision_selected_assets": sorted(decision_selected_ever),
        "selected_identity_mismatches": selected_identity_mismatches,
        "asset_contribution": asset_contribution,
        "regime_contribution": regime_contribution,
        "cash_contribution": cash_contribution,
        "decision_count": len(decisions),
        "maximum_gross_exposure": maximum_gross_exposure,
        "maximum_target_exposure": maximum_target_exposure,
        "macro_state_counts": macro_state_counts,
        "target_multiplier_counts": multiplier_counts,
    }


def _compound(values: list[float]) -> float:
    return float(np.prod([1.0 + value for value in values]) - 1.0)


def build_walk_forward_folds(
    dataset: Dataset,
    cash_history: v44.CashRateHistory,
    snapshots: dict[datetime, MacroSnapshot],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for fold in v46.WALK_FORWARD_FOLDS:
        bundle, training_report = v46.train_base_bundle(dataset, fold)
        predictions = v43.predict_components(bundle, dataset.X)
        validation_mask = v46.date_mask(
            dataset,
            fold.validation_start,
            fold.validation_end,
        )
        baseline_standard = v44.simulate(
            dataset,
            validation_mask,
            bundle,
            predictions,
            cash_history,
            one_way_cost=STANDARD_ONE_WAY_COST,
        )
        baseline_stress = v44.simulate(
            dataset,
            validation_mask,
            bundle,
            predictions,
            cash_history,
            one_way_cost=STRESS_ONE_WAY_COST,
        )
        result.append({
            "spec": fold,
            "bundle": bundle,
            "predictions": predictions,
            "validation_mask": validation_mask,
            "training_report": training_report,
            "baseline_standard": baseline_standard,
            "baseline_stress": baseline_stress,
        })
    return result


def active_eligibility(
    fold_results: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    standard_excess = [
        float(value["macro_standard"]["net_return"])
        - float(value["baseline_standard"]["net_return"])
        for value in fold_results
    ]
    stress_excess = [
        float(value["macro_stress"]["net_return"])
        - float(value["baseline_stress"]["net_return"])
        for value in fold_results
    ]
    if sum(value > 0.0 for value in standard_excess) < 4:
        reasons.append("fewer_than_four_positive_standard_excess_folds")
    if _compound(standard_excess) <= 0.0:
        reasons.append("non_positive_compounded_standard_excess")
    if _compound(stress_excess) <= 0.0:
        reasons.append("non_positive_compounded_stress_excess")
    if min(standard_excess) < -0.003:
        reasons.append("worst_standard_fold_excess_below_minus_0_30_percent")
    for value in fold_results:
        macro_drawdown = max(
            float(value["macro_standard"]["maximum_drawdown"]),
            float(value["macro_stress"]["maximum_drawdown"]),
        )
        baseline_drawdown = max(
            float(value["baseline_standard"]["maximum_drawdown"]),
            float(value["baseline_stress"]["maximum_drawdown"]),
        )
        if macro_drawdown > baseline_drawdown + 0.005:
            reasons.append("drawdown_allowance_exceeded")
            break
    if any(
        float(value["macro_standard"]["maximum_target_exposure"])
        > 0.1500001
        or float(value["macro_stress"]["maximum_target_exposure"])
        > 0.1500001
        for value in fold_results
    ):
        reasons.append("target_exposure_exceeded")
    if any(
        int(value["macro_standard"]["selected_identity_mismatches"]) != 0
        or int(value["macro_stress"]["selected_identity_mismatches"]) != 0
        for value in fold_results
    ):
        reasons.append("selected_asset_identity_changed")
    return not reasons, reasons


def _selection_key(
    fold_results: list[dict[str, Any]],
    config: MacroConfig,
) -> tuple[float, ...]:
    standard_excess = [
        float(value["macro_standard"]["net_return"])
        - float(value["baseline_standard"]["net_return"])
        for value in fold_results
    ]
    stress_excess = [
        float(value["macro_stress"]["net_return"])
        - float(value["baseline_stress"]["net_return"])
        for value in fold_results
    ]
    maximum_drawdown = max(
        max(
            float(value["macro_standard"]["maximum_drawdown"]),
            float(value["macro_stress"]["maximum_drawdown"]),
        )
        for value in fold_results
    )
    turnover = sum(
        float(value["macro_standard"]["turnover"])
        for value in fold_results
    )
    complexity = (
        abs(config.supportive_multiplier - 1.0)
        + abs(config.defensive_multiplier - 1.0)
        + abs(config.supportive_threshold - 0.35)
        + abs(config.defensive_threshold - 0.65)
    )
    return (
        min(standard_excess),
        float(sum(value > 0.0 for value in standard_excess)),
        _compound(stress_excess),
        _compound(standard_excess),
        -maximum_drawdown,
        -turnover,
        -complexity,
        -config.supportive_multiplier,
        config.defensive_multiplier,
    )


def select_macro_config(
    dataset: Dataset,
    cash_history: v44.CashRateHistory,
    snapshots: dict[datetime, MacroSnapshot],
    folds: list[dict[str, Any]],
) -> tuple[MacroConfig, dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    eligible_active: list[
        tuple[tuple[float, ...], MacroConfig, list[dict[str, Any]]]
    ] = []
    disabled_results: list[dict[str, Any]] | None = None
    for config in macro_grid():
        fold_results: list[dict[str, Any]] = []
        for fold in folds:
            macro_standard = simulate_macro(
                dataset,
                fold["validation_mask"],
                fold["bundle"],
                fold["predictions"],
                cash_history,
                snapshots,
                config,
                one_way_cost=STANDARD_ONE_WAY_COST,
            )
            macro_stress = simulate_macro(
                dataset,
                fold["validation_mask"],
                fold["bundle"],
                fold["predictions"],
                cash_history,
                snapshots,
                config,
                one_way_cost=STRESS_ONE_WAY_COST,
            )
            fold_results.append({
                "name": fold["spec"].name,
                "baseline_standard": fold["baseline_standard"],
                "baseline_stress": fold["baseline_stress"],
                "macro_standard": macro_standard,
                "macro_stress": macro_stress,
            })
        standard_excess = [
            float(value["macro_standard"]["net_return"])
            - float(value["baseline_standard"]["net_return"])
            for value in fold_results
        ]
        stress_excess = [
            float(value["macro_stress"]["net_return"])
            - float(value["baseline_stress"]["net_return"])
            for value in fold_results
        ]
        if config.disabled:
            disabled_results = fold_results
            eligible = True
            reasons: list[str] = []
            key = None
        else:
            eligible, reasons = active_eligibility(fold_results)
            key = _selection_key(fold_results, config) if eligible else None
            if eligible and key is not None:
                eligible_active.append((key, config, fold_results))
        candidates.append({
            "config": asdict(config),
            "disabled_baseline": config.disabled,
            "eligible": eligible,
            "ineligibility_reasons": reasons,
            "selection_key": list(key) if key is not None else None,
            "positive_standard_excess_folds": sum(
                value > 0.0 for value in standard_excess
            ),
            "minimum_standard_excess": min(standard_excess),
            "compounded_standard_excess": _compound(standard_excess),
            "compounded_stress_excess": _compound(stress_excess),
        })

    if disabled_results is None:
        raise MacroRiskConfirmationV47Error(
            "disabled macro baseline was not evaluated"
        )
    if eligible_active:
        best = max(eligible_active, key=lambda value: value[0])
        selected_key, selected_config, selected_results = best
    else:
        selected_key = None
        selected_config = DISABLED_MACRO
        selected_results = disabled_results

    selected_folds: list[dict[str, Any]] = []
    for fold, values in zip(folds, selected_results, strict=True):
        selected_folds.append({
            "name": fold["spec"].name,
            "training": fold["training_report"],
            "baseline_standard": values["baseline_standard"],
            "baseline_stress": values["baseline_stress"],
            "macro_standard": values["macro_standard"],
            "macro_stress": values["macro_stress"],
            "standard_excess": (
                float(values["macro_standard"]["net_return"])
                - float(values["baseline_standard"]["net_return"])
            ),
            "stress_excess": (
                float(values["macro_stress"]["net_return"])
                - float(values["baseline_stress"]["net_return"])
            ),
        })
    return selected_config, {
        "selected_config": asdict(selected_config),
        "selected_is_disabled_baseline": selected_config.disabled,
        "selected_key": (
            list(selected_key) if selected_key is not None else None
        ),
        "walk_forward_fold_count": len(folds),
        "candidate_count": len(candidates),
        "eligible_active_candidate_count": len(eligible_active),
        "folds": selected_folds,
        "candidates": candidates,
    }


def evaluate_final(
    dataset: Dataset,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    cash_history: v44.CashRateHistory,
    snapshots: dict[datetime, MacroSnapshot],
    config: MacroConfig,
    *,
    v44_baseline: dict[str, Any],
) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    for name, start, end in v43.SEALED_WINDOWS:
        mask = v46.date_mask(dataset, start, end)
        standard = simulate_macro(
            dataset,
            mask,
            bundle,
            predictions,
            cash_history,
            snapshots,
            config,
            one_way_cost=STANDARD_ONE_WAY_COST,
        )
        stress = simulate_macro(
            dataset,
            mask,
            bundle,
            predictions,
            cash_history,
            snapshots,
            config,
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
    decision_selected_assets = sorted(set().union(*[
        set(value["standard"]["decision_selected_assets"])
        for value in windows
    ]))
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    regime_contribution = {
        name: 0.0 for name in REGIME_NAMES.values()
    }
    cash_contribution = 0.0
    macro_state_counts = {
        "supportive": 0,
        "neutral": 0,
        "defensive": 0,
    }
    multiplier_counts: dict[str, int] = {}
    maximum_target_exposure = 0.0
    identity_mismatches = 0
    for value in windows:
        standard = value["standard"]
        cash_contribution += float(standard["cash_contribution"])
        maximum_target_exposure = max(
            maximum_target_exposure,
            float(standard["maximum_target_exposure"]),
        )
        identity_mismatches += int(
            standard["selected_identity_mismatches"]
        )
        for state, count in standard["macro_state_counts"].items():
            macro_state_counts[state] += int(count)
        for multiplier, count in standard[
            "target_multiplier_counts"
        ].items():
            multiplier_counts[multiplier] = (
                multiplier_counts.get(multiplier, 0) + int(count)
            )
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
        "decision_selected_assets": decision_selected_assets,
        "selected_identity_mismatches": identity_mismatches,
        "asset_contribution": asset_contribution,
        "regime_contribution": regime_contribution,
        "cash_contribution": cash_contribution,
        "maximum_positive_asset_share": asset_share,
        "maximum_positive_window_share": window_share,
        "maximum_positive_regime_share": regime_share,
        "maximum_target_exposure": maximum_target_exposure,
        "macro_state_counts": macro_state_counts,
        "target_multiplier_counts": multiplier_counts,
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
            "drawdown_change": maximum_drawdown - float(
                v44_baseline["maximum_drawdown"]
            ),
            "action_count_change": actions - int(
                v44_baseline["target_changing_actions"]
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
    macro_history: MacroHistory | None = None,
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
        raise MacroRiskConfirmationV47Error(
            "current crypto source report is unavailable"
        )
    if canonical_json(source_report) != canonical_json(
        baseline_report["source"]
    ):
        raise MacroRiskConfirmationV47Error(
            "current crypto source inventory differs from frozen v4.3"
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
        raise MacroRiskConfirmationV47Error(
            "current dataset metadata differs from frozen v4.3"
        )
    if canonical_json(v43.bundle_summary(final_bundle)) != canonical_json(
        baseline_report["bundle"]
    ):
        raise MacroRiskConfirmationV47Error(
            "final bundle differs from frozen v4.3 report"
        )
    reproduced_v43 = v43.evaluate_sealed(dataset, final_bundle)
    if canonical_json(reproduced_v43) != canonical_json(
        baseline_report["evaluation"]
    ):
        raise MacroRiskConfirmationV47Error(
            "final bundle does not reproduce frozen v4.3"
        )
    if cash_history is None:
        cash_history = v44.load_cash_history()
    if macro_history is None:
        macro_history = load_macro_history()
    snapshots = build_macro_snapshots(dataset.dates, macro_history)
    folds = build_walk_forward_folds(
        dataset,
        cash_history,
        snapshots,
    )
    selected_config, selection = select_macro_config(
        dataset,
        cash_history,
        snapshots,
        folds,
    )
    final_predictions = v43.predict_components(final_bundle, dataset.X)
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
        snapshots,
        selected_config,
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
        "macro_source": macro_history.source,
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
            "final_v43_retrained_for_v47": False,
            "walk_forward_fold_count": len(folds),
            "macro_same_day_observations_allowed": False,
        },
        "selection": selection,
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
        description="Run v4.7 macro-risk confirmation research"
    )
    parser.add_argument("--baseline-json", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("evidence/v47/historical.json"),
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
        "selected_is_disabled_baseline": (
            selection["selected_is_disabled_baseline"]
        ),
        "eligible_active_candidate_count": (
            selection["eligible_active_candidate_count"]
        ),
        "standard_return": evaluation["aggregate_standard_return"],
        "stress_return": evaluation["aggregate_stress_return"],
        "annualized_standard_return": evaluation[
            "annualized_standard_return"
        ],
        "maximum_drawdown": evaluation["maximum_drawdown"],
        "standard_return_change": evaluation["v44_comparison"][
            "standard_return_change"
        ],
        "macro_state_counts": evaluation["macro_state_counts"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
