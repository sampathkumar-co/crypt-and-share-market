from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import io
import json
import math
import platform
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np

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

SCHEMA_VERSION = "4.7-macro-liquidity-state"
PROTOCOL_PATH = Path("research/V47_MACRO_LIQUIDITY_STATE_PROTOCOL.md")
CONTRACT_PATH = Path(
    "research/V471_MACRO_LIQUIDITY_STATE_IMPLEMENTATION_CONTRACT.md"
)
FRED_START = "2022-01-01"
FRED_END = "2026-06-30"
FRED_PROVIDER = "fred-federal-reserve-public-csv"
MIN_SERIES_OBSERVATIONS = 900
SERIES_META: dict[str, dict[str, Any]] = {
    "VIXCLS": {"positive": True, "units": "index"},
    "DTWEXBGS": {"positive": True, "units": "index"},
    "DGS10": {"positive": True, "units": "percent"},
    "NASDAQCOM": {"positive": True, "units": "index"},
}
FRED_URLS = {
    series: (
        "https://fred.stlouisfed.org/graph/fredgraph.csv?"
        f"id={series}&cosd={FRED_START}&coed={FRED_END}"
    )
    for series in SERIES_META
}

MACRO_FEATURE_NAMES = [
    "vix_relative_60",
    "vix_change_5",
    "vix_change_20",
    "vix_change_60",
    "dollar_change_5",
    "dollar_change_20",
    "dollar_change_60",
    "dgs10_level",
    "dgs10_change_5",
    "dgs10_change_20",
    "dgs10_change_60",
    "nasdaq_return_5",
    "nasdaq_return_20",
    "nasdaq_return_60",
    "risk_on_composite",
]
FAMILY_COLUMNS = {
    "risk_appetite": (0, 1, 2, 3, 11, 12, 13),
    "dollar_rates": (4, 5, 6, 7, 8, 9, 10),
    "full_macro": tuple(range(len(MACRO_FEATURE_NAMES))),
}
THRESHOLD_GRID: tuple[float | None, ...] = (
    None,
    0.35,
    0.40,
    0.45,
    0.50,
    0.55,
    0.60,
    0.65,
)


class MacroLiquidityStateV47Error(RuntimeError):
    pass


@dataclass(frozen=True)
class MacroSeries:
    values: dict[datetime, float]
    source: dict[str, Any]

    @property
    def dates(self) -> tuple[datetime, ...]:
        return tuple(sorted(self.values))


@dataclass(frozen=True)
class MacroHistory:
    series: dict[str, MacroSeries]

    @property
    def source(self) -> dict[str, Any]:
        return {
            "provider": FRED_PROVIDER,
            "requested_start": FRED_START,
            "requested_end": FRED_END,
            "series": {
                name: value.source
                for name, value in sorted(self.series.items())
            },
        }


@dataclass(frozen=True)
class FamilyFoldResult:
    fold: str
    family: str
    threshold: float | None
    training_date_count: int
    positive_label_share: float
    calibration_baseline: dict[str, Any]
    calibration_gated: dict[str, Any]
    calibration_excess: float
    validation_baseline: dict[str, Any]
    validation_gated: dict[str, Any]
    validation_excess: float


def parse_fred_series(content: bytes, series: str) -> dict[datetime, float]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MacroLiquidityStateV47Error(
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
        raise MacroLiquidityStateV47Error(
            f"{series} CSV columns unavailable: {fields}"
        )
    positive_required = bool(SERIES_META[series]["positive"])
    values: dict[datetime, float] = {}
    for row in reader:
        raw = str(row.get(series, "")).strip().replace(",", "")
        if not raw or raw == ".":
            continue
        try:
            stamp = datetime.fromisoformat(
                str(row[date_column]).strip()
            ).replace(tzinfo=timezone.utc)
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise MacroLiquidityStateV47Error(
                f"invalid {series} observation: {row}"
            ) from exc
        if stamp in values:
            raise MacroLiquidityStateV47Error(
                f"duplicate {series} observation: {stamp.date()}"
            )
        if not math.isfinite(value):
            raise MacroLiquidityStateV47Error(
                f"non-finite {series} observation on {stamp.date()}"
            )
        if positive_required and value <= 0.0:
            raise MacroLiquidityStateV47Error(
                f"non-positive {series} observation on {stamp.date()}"
            )
        values[stamp] = value
    if not values:
        raise MacroLiquidityStateV47Error(
            f"{series} CSV has no usable observations"
        )
    return values


def load_macro_history(timeout: float = 30.0) -> MacroHistory:
    series_map: dict[str, MacroSeries] = {}
    for series, url in FRED_URLS.items():
        request = Request(
            url,
            headers={
                "User-Agent": "tradebot-v47-macro-liquidity/1.0",
                "Accept": "text/csv,*/*;q=0.8",
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                if response.status != 200:
                    raise MacroLiquidityStateV47Error(
                        f"{series} source returned HTTP {response.status}"
                    )
                raw = response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            raise MacroLiquidityStateV47Error(
                f"{series} source download failed: {exc}"
            ) from exc
        if not raw:
            raise MacroLiquidityStateV47Error(
                f"{series} source returned an empty response"
            )
        values = parse_fred_series(raw, series)
        if len(values) < MIN_SERIES_OBSERVATIONS:
            raise MacroLiquidityStateV47Error(
                f"{series} has only {len(values)} observations; "
                f"minimum is {MIN_SERIES_OBSERVATIONS}"
            )
        dates = sorted(values)
        series_map[series] = MacroSeries(
            values=values,
            source={
                "provider": FRED_PROVIDER,
                "series": series,
                "url": url,
                "units": SERIES_META[series]["units"],
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "observation_count": len(values),
                "first_date": dates[0].date().isoformat(),
                "last_date": dates[-1].date().isoformat(),
            },
        )
    return MacroHistory(series_map)


def value_at_or_before(series: MacroSeries, cutoff: datetime) -> tuple[datetime, float]:
    dates = series.dates
    index = bisect.bisect_right(dates, cutoff) - 1
    if index < 0:
        raise MacroLiquidityStateV47Error(
            f"no {series.source['series']} observation known by {utc_iso(cutoff)}"
        )
    stamp = dates[index]
    return stamp, float(series.values[stamp])


def prior_known_value(
    history: MacroHistory,
    series: str,
    decision_stamp: datetime,
    *,
    lookback_days: int = 0,
) -> tuple[datetime, float]:
    cutoff = decision_stamp - timedelta(days=1 + lookback_days)
    return value_at_or_before(history.series[series], cutoff)


def trailing_known_values(
    history: MacroHistory,
    series: str,
    decision_stamp: datetime,
    count: int,
) -> np.ndarray:
    cutoff = decision_stamp - timedelta(days=1)
    item = history.series[series]
    index = bisect.bisect_right(item.dates, cutoff)
    if index < count:
        raise MacroLiquidityStateV47Error(
            f"{series} lacks {count} trailing observations by "
            f"{decision_stamp.date()}"
        )
    return np.asarray(
        [item.values[stamp] for stamp in item.dates[index - count:index]],
        dtype=float,
    )


def _ratio_change(now: float, previous: float) -> float:
    if previous <= 0.0:
        raise MacroLiquidityStateV47Error(
            "macro ratio denominator must be positive"
        )
    return now / previous - 1.0


def macro_feature_vector(
    history: MacroHistory,
    decision_stamp: datetime,
) -> np.ndarray:
    now = {
        series: prior_known_value(history, series, decision_stamp)[1]
        for series in SERIES_META
    }
    lookbacks = {
        (series, days): prior_known_value(
            history,
            series,
            decision_stamp,
            lookback_days=days,
        )[1]
        for series in SERIES_META
        for days in (5, 20, 60)
    }
    vix_trailing = trailing_known_values(
        history,
        "VIXCLS",
        decision_stamp,
        60,
    )
    vix_relative = now["VIXCLS"] / float(np.mean(vix_trailing)) - 1.0
    vix_changes = [
        _ratio_change(now["VIXCLS"], lookbacks[("VIXCLS", days)])
        for days in (5, 20, 60)
    ]
    dollar_changes = [
        _ratio_change(now["DTWEXBGS"], lookbacks[("DTWEXBGS", days)])
        for days in (5, 20, 60)
    ]
    yield_level = now["DGS10"] / 100.0
    yield_changes = [
        (now["DGS10"] - lookbacks[("DGS10", days)]) / 100.0
        for days in (5, 20, 60)
    ]
    nasdaq_returns = [
        _ratio_change(now["NASDAQCOM"], lookbacks[("NASDAQCOM", days)])
        for days in (5, 20, 60)
    ]
    risk_on = (
        nasdaq_returns[1]
        - vix_changes[1]
        - dollar_changes[1]
        - 2.0 * yield_changes[1]
    )
    result = np.asarray([
        vix_relative,
        *vix_changes,
        *dollar_changes,
        yield_level,
        *yield_changes,
        *nasdaq_returns,
        risk_on,
    ], dtype=float)
    if result.shape != (len(MACRO_FEATURE_NAMES),):
        raise MacroLiquidityStateV47Error(
            f"unexpected macro feature shape: {result.shape}"
        )
    if not np.all(np.isfinite(result)):
        raise MacroLiquidityStateV47Error(
            f"non-finite macro features for {decision_stamp.date()}"
        )
    return result


def build_macro_matrix(
    dataset: Dataset,
    history: MacroHistory,
) -> tuple[np.ndarray, dict[datetime, np.ndarray]]:
    by_date = {
        stamp: macro_feature_vector(history, stamp)
        for stamp in sorted(set(dataset.dates))
    }
    matrix = np.vstack([by_date[stamp] for stamp in dataset.dates])
    return matrix, by_date


def unique_date_indexes(dataset: Dataset) -> dict[datetime, list[int]]:
    result: dict[datetime, list[int]] = {}
    for index, stamp in enumerate(dataset.dates):
        result.setdefault(stamp, []).append(index)
    return result


def date_level_samples(
    dataset: Dataset,
    macro_by_date: dict[datetime, np.ndarray],
    family: str,
    *,
    start: datetime | None,
    end: datetime,
) -> tuple[np.ndarray, np.ndarray, list[datetime]]:
    if family not in FAMILY_COLUMNS:
        raise MacroLiquidityStateV47Error(f"unknown macro family: {family}")
    columns = FAMILY_COLUMNS[family]
    rows: list[np.ndarray] = []
    labels: list[int] = []
    dates: list[datetime] = []
    for stamp, indexes in sorted(unique_date_indexes(dataset).items()):
        if stamp > end or (start is not None and stamp < start):
            continue
        rows.append(macro_by_date[stamp][list(columns)])
        labels.append(int(float(np.mean(dataset.return3[indexes])) > 0.0))
        dates.append(stamp)
    if len(rows) < 120:
        raise MacroLiquidityStateV47Error(
            f"{family} has only {len(rows)} date-level training samples"
        )
    return np.vstack(rows), np.asarray(labels, dtype=int), dates


def fit_macro_classifier(X: np.ndarray, y: np.ndarray) -> Any:
    from sklearn.dummy import DummyClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    unique = np.unique(y)
    if len(unique) == 1:
        return DummyClassifier(
            strategy="constant",
            constant=int(unique[0]),
        ).fit(X, y)
    return Pipeline([
        ("scale", StandardScaler()),
        ("model", LogisticRegression(
            C=0.5,
            class_weight="balanced",
            max_iter=2_000,
            random_state=47,
        )),
    ]).fit(X, y)


def positive_probabilities(model: Any, X: np.ndarray) -> np.ndarray:
    raw = model.predict_proba(X)
    classes = [int(value) for value in model.classes_]
    if 1 not in classes:
        return np.zeros(len(X), dtype=float)
    return np.asarray(raw[:, classes.index(1)], dtype=float)


def probability_by_date(
    model: Any,
    macro_by_date: dict[datetime, np.ndarray],
    family: str,
) -> dict[datetime, float]:
    dates = sorted(macro_by_date)
    columns = FAMILY_COLUMNS[family]
    X = np.vstack([
        macro_by_date[stamp][list(columns)] for stamp in dates
    ])
    values = positive_probabilities(model, X)
    return {
        stamp: float(value)
        for stamp, value in zip(dates, values, strict=True)
    }


def macro_gated_decisions(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    probabilities: dict[datetime, float],
    threshold: float | None,
) -> dict[datetime, dict[str, Any]]:
    baseline = v43.decisions_by_date(dataset, mask, bundle, predictions)
    result: dict[datetime, dict[str, Any]] = {}
    for stamp, decision in baseline.items():
        selected = list(decision["selected"])
        gated_assets: list[str] = []
        probability = float(probabilities[stamp])
        if (
            threshold is not None
            and decision["regime"] != 2
            and selected
            and probability < threshold
        ):
            gated_assets = sorted(dataset.assets[index] for index in selected)
            selected = []
        result[stamp] = {
            **decision,
            "selected": selected,
            "macro_probability": probability,
            "macro_threshold": threshold,
            "gated_assets": gated_assets,
        }
    return result


def simulate_macro_gate(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    history: v44.CashRateHistory,
    probabilities: dict[datetime, float],
    threshold: float | None,
    *,
    one_way_cost: float,
) -> dict[str, Any]:
    decisions = macro_gated_decisions(
        dataset,
        mask,
        bundle,
        predictions,
        probabilities,
        threshold,
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
    gated_ever: set[str] = set()
    peak = 1.0
    maximum_drawdown = 0.0
    turnover = 0.0
    action_count = 0
    age = 3
    maximum_gross_exposure = 0.0
    maximum_target_exposure = 0.0
    maximum_selected_cardinality = 0
    gated_decision_count = 0
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    regime_contribution = {
        name: 0.0 for name in REGIME_NAMES.values()
    }
    cash_contribution = 0.0

    for stamp in sorted(decisions):
        equity_before = cash + sum(holdings.values())
        decision = decisions[stamp]
        if decision["gated_assets"]:
            gated_decision_count += 1
            gated_ever.update(decision["gated_assets"])
        panic = decision["regime"] == 2
        due = age >= 3
        target_assets = selected_assets
        if panic:
            target_assets = ()
        elif due:
            target_assets = tuple(
                dataset.assets[index] for index in decision["selected"]
            )
        maximum_selected_cardinality = max(
            maximum_selected_cardinality,
            len(target_assets),
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
                cash -= one_way_cost * traded
                turnover += traded
                action_count += 1
            cash += sum(
                holdings[asset] - target_values[asset] for asset in ASSETS
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
        "gated_assets": sorted(gated_ever),
        "gated_decision_count": gated_decision_count,
        "asset_contribution": asset_contribution,
        "regime_contribution": regime_contribution,
        "cash_contribution": cash_contribution,
        "decision_count": len(decisions),
        "maximum_gross_exposure": maximum_gross_exposure,
        "maximum_target_exposure": maximum_target_exposure,
        "maximum_selected_cardinality": maximum_selected_cardinality,
        "never_added_asset": True,
    }


def threshold_key(
    gated: dict[str, Any],
    baseline: dict[str, Any],
    threshold: float | None,
) -> tuple[float, ...]:
    excess = float(gated["net_return"]) - float(baseline["net_return"])
    drawdown_penalty = max(
        0.0,
        float(gated["maximum_drawdown"])
        - float(baseline["maximum_drawdown"]),
    )
    turnover_penalty = max(
        0.0,
        float(gated["turnover"]) - float(baseline["turnover"]),
    )
    score = excess - 1.5 * drawdown_penalty - 0.10 * turnover_penalty
    if threshold is not None and gated["gated_decision_count"] == 0:
        score -= 1.0
    return (
        score,
        excess,
        -float(gated["maximum_drawdown"]),
        -float(gated["turnover"]),
        -float(gated["target_changing_actions"]),
        -float(gated["gated_decision_count"]),
        -(threshold if threshold is not None else -1.0),
    )


def fit_and_evaluate_family_fold(
    dataset: Dataset,
    macro_by_date: dict[datetime, np.ndarray],
    cash_history: v44.CashRateHistory,
    fold: dict[str, Any],
    family: str,
) -> FamilyFoldResult:
    spec = fold["spec"]
    X_train, y_train, training_dates = date_level_samples(
        dataset,
        macro_by_date,
        family,
        start=None,
        end=spec.training_end,
    )
    model = fit_macro_classifier(X_train, y_train)
    probabilities = probability_by_date(model, macro_by_date, family)
    calibration_mask = v43.date_mask(
        dataset,
        spec.base_calibration_start,
        spec.base_calibration_end,
    )
    calibration_baseline = v44.simulate(
        dataset,
        calibration_mask,
        fold["bundle"],
        fold["predictions"],
        cash_history,
        one_way_cost=STANDARD_ONE_WAY_COST,
    )
    best: tuple[
        tuple[float, ...], float | None, dict[str, Any]
    ] | None = None
    for threshold in THRESHOLD_GRID:
        gated = simulate_macro_gate(
            dataset,
            calibration_mask,
            fold["bundle"],
            fold["predictions"],
            cash_history,
            probabilities,
            threshold,
            one_way_cost=STANDARD_ONE_WAY_COST,
        )
        key = threshold_key(gated, calibration_baseline, threshold)
        if best is None or key > best[0]:
            best = (key, threshold, gated)
    if best is None:
        raise MacroLiquidityStateV47Error(
            f"{family} {spec.name} produced no threshold"
        )
    validation_gated = simulate_macro_gate(
        dataset,
        fold["validation_mask"],
        fold["bundle"],
        fold["predictions"],
        cash_history,
        probabilities,
        best[1],
        one_way_cost=STANDARD_ONE_WAY_COST,
    )
    return FamilyFoldResult(
        fold=spec.name,
        family=family,
        threshold=best[1],
        training_date_count=len(training_dates),
        positive_label_share=float(np.mean(y_train)),
        calibration_baseline=calibration_baseline,
        calibration_gated=best[2],
        calibration_excess=(
            float(best[2]["net_return"])
            - float(calibration_baseline["net_return"])
        ),
        validation_baseline=fold["baseline"],
        validation_gated=validation_gated,
        validation_excess=(
            float(validation_gated["net_return"])
            - float(fold["baseline"]["net_return"])
        ),
    )


def compounded_excess(results: list[FamilyFoldResult]) -> float:
    gated = float(np.prod([
        1.0 + value.validation_gated["net_return"]
        for value in results
    ]))
    baseline = float(np.prod([
        1.0 + value.validation_baseline["net_return"]
        for value in results
    ]))
    return gated / max(baseline, 1e-12) - 1.0


def family_eligibility(
    family: str,
    results: list[FamilyFoldResult],
) -> tuple[bool, list[str]]:
    if family == "disabled":
        return True, []
    reasons: list[str] = []
    excess = [value.validation_excess for value in results]
    if compounded_excess(results) <= 0.0:
        reasons.append("non_positive_compounded_excess")
    if sum(value > 0.0 for value in excess) < 4:
        reasons.append("fewer_than_four_positive_excess_folds")
    if min(excess) < -0.0025:
        reasons.append("minimum_fold_excess_below_allowance")
    if sum(
        int(value.validation_gated["target_changing_actions"])
        for value in results
    ) > sum(
        int(value.validation_baseline["target_changing_actions"])
        for value in results
    ):
        reasons.append("increased_actions")
    if sum(
        float(value.validation_gated["turnover"])
        for value in results
    ) > sum(
        float(value.validation_baseline["turnover"])
        for value in results
    ) + 1e-12:
        reasons.append("increased_turnover")
    if any(
        float(value.validation_gated["maximum_drawdown"])
        > float(value.validation_baseline["maximum_drawdown"]) + 0.0025
        for value in results
    ):
        reasons.append("drawdown_allowance_exceeded")
    if sum(
        int(value.validation_gated["gated_decision_count"])
        for value in results
    ) == 0:
        reasons.append("no_validation_intervention")
    return not reasons, reasons


def family_selection_key(
    family: str,
    results: list[FamilyFoldResult],
) -> tuple[Any, ...]:
    excess = [value.validation_excess for value in results]
    return (
        min(excess),
        sum(value > 0.0 for value in excess),
        compounded_excess(results),
        min(float(value.validation_gated["net_return"]) for value in results),
        -max(float(value.validation_gated["maximum_drawdown"]) for value in results),
        -sum(float(value.validation_gated["turnover"]) for value in results),
        -sum(int(value.validation_gated["target_changing_actions"]) for value in results),
        -sum(int(value.validation_gated["gated_decision_count"]) for value in results),
        family,
    )


def select_macro_family(
    family_results: dict[str, list[FamilyFoldResult]],
) -> tuple[str, dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    best: tuple[tuple[Any, ...], str] | None = None
    disabled_results: list[FamilyFoldResult] = []
    exemplar = next(iter(family_results.values()))
    for value in exemplar:
        disabled_results.append(FamilyFoldResult(
            fold=value.fold,
            family="disabled",
            threshold=None,
            training_date_count=value.training_date_count,
            positive_label_share=value.positive_label_share,
            calibration_baseline=value.calibration_baseline,
            calibration_gated=value.calibration_baseline,
            calibration_excess=0.0,
            validation_baseline=value.validation_baseline,
            validation_gated=value.validation_baseline,
            validation_excess=0.0,
        ))
    all_results = {"disabled": disabled_results, **family_results}
    for family, results in all_results.items():
        eligible, reasons = family_eligibility(family, results)
        key = family_selection_key(family, results) if eligible else None
        candidate = {
            "family": family,
            "eligible": eligible,
            "ineligibility_reasons": reasons,
            "selection_key": list(key) if key is not None else None,
            "minimum_fold_excess": min(
                value.validation_excess for value in results
            ),
            "positive_excess_fold_count": sum(
                value.validation_excess > 0.0 for value in results
            ),
            "compounded_excess": compounded_excess(results),
            "gated_decision_count": sum(
                int(value.validation_gated["gated_decision_count"])
                for value in results
            ),
        }
        candidates.append(candidate)
        if eligible and (best is None or key > best[0]):
            best = (key, family)
    if best is None:
        raise MacroLiquidityStateV47Error(
            "macro family selection produced no disabled fallback"
        )
    selected_results = all_results[best[1]]
    return best[1], {
        "selected_family": best[1],
        "selected_is_disabled_baseline": best[1] == "disabled",
        "selected_key": list(best[0]),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "folds": [asdict(value) for value in selected_results],
    }


def fit_final_family(
    dataset: Dataset,
    macro_by_date: dict[datetime, np.ndarray],
    cash_history: v44.CashRateHistory,
    bundle: v43.Bundle,
    family: str,
) -> tuple[dict[datetime, float], float | None, dict[str, Any]]:
    if family == "disabled":
        return (
            {stamp: 1.0 for stamp in set(dataset.dates)},
            None,
            {
                "family": "disabled",
                "threshold": None,
                "training_date_count": 0,
                "positive_label_share": None,
                "calibration_excess": 0.0,
                "gated_decision_count": 0,
            },
        )
    X_train, y_train, training_dates = date_level_samples(
        dataset,
        macro_by_date,
        family,
        start=None,
        end=v43.TRAIN_END,
    )
    model = fit_macro_classifier(X_train, y_train)
    probabilities = probability_by_date(model, macro_by_date, family)
    predictions = v43.predict_components(bundle, dataset.X)
    calibration_mask = v43.calibration_mask(dataset)
    baseline = v44.simulate(
        dataset,
        calibration_mask,
        bundle,
        predictions,
        cash_history,
        one_way_cost=STANDARD_ONE_WAY_COST,
    )
    best: tuple[
        tuple[float, ...], float | None, dict[str, Any]
    ] | None = None
    for threshold in THRESHOLD_GRID:
        gated = simulate_macro_gate(
            dataset,
            calibration_mask,
            bundle,
            predictions,
            cash_history,
            probabilities,
            threshold,
            one_way_cost=STANDARD_ONE_WAY_COST,
        )
        key = threshold_key(gated, baseline, threshold)
        if best is None or key > best[0]:
            best = (key, threshold, gated)
    if best is None:
        raise MacroLiquidityStateV47Error(
            "final macro calibration produced no threshold"
        )
    return probabilities, best[1], {
        "family": family,
        "threshold": best[1],
        "training_date_count": len(training_dates),
        "positive_label_share": float(np.mean(y_train)),
        "baseline": baseline,
        "gated": best[2],
        "calibration_excess": (
            float(best[2]["net_return"]) - float(baseline["net_return"])
        ),
        "gated_decision_count": int(best[2]["gated_decision_count"]),
    }


def evaluate_sealed(
    dataset: Dataset,
    bundle: v43.Bundle,
    cash_history: v44.CashRateHistory,
    probabilities: dict[datetime, float],
    threshold: float | None,
) -> dict[str, Any]:
    predictions = v43.predict_components(bundle, dataset.X)
    windows: list[dict[str, Any]] = []
    for name, start, end in v43.SEALED_WINDOWS:
        mask = v43.date_mask(dataset, start, end)
        standard = simulate_macro_gate(
            dataset,
            mask,
            bundle,
            predictions,
            cash_history,
            probabilities,
            threshold,
            one_way_cost=STANDARD_ONE_WAY_COST,
        )
        stress = simulate_macro_gate(
            dataset,
            mask,
            bundle,
            predictions,
            cash_history,
            probabilities,
            threshold,
            one_way_cost=STRESS_ONE_WAY_COST,
        )
        days = len({dataset.dates[index] for index in np.flatnonzero(mask)})
        standard["verification_days"] = days
        stress["verification_days"] = days
        windows.append({
            "name": name,
            "start": utc_iso(start),
            "end": utc_iso(end),
            "standard": standard,
            "stress": stress,
        })
    standard_returns = [float(value["standard"]["net_return"]) for value in windows]
    stress_returns = [float(value["stress"]["net_return"]) for value in windows]
    aggregate_standard = float(np.prod([1.0 + value for value in standard_returns]) - 1.0)
    aggregate_stress = float(np.prod([1.0 + value for value in stress_returns]) - 1.0)
    verification_days = sum(int(value["standard"]["verification_days"]) for value in windows)
    annualized = (
        (1.0 + aggregate_standard) ** (365.0 / verification_days) - 1.0
        if verification_days > 0 and aggregate_standard > -1.0
        else -1.0
    )
    maximum_drawdown = max(
        max(float(value["standard"]["maximum_drawdown"]), float(value["stress"]["maximum_drawdown"]))
        for value in windows
    )
    actions = sum(int(value["standard"]["target_changing_actions"]) for value in windows)
    selected_assets = sorted(set().union(*[
        set(value["standard"]["selected_assets"]) for value in windows
    ]))
    gated_assets = sorted(set().union(*[
        set(value["standard"]["gated_assets"]) for value in windows
    ]))
    gated_decisions = sum(int(value["standard"]["gated_decision_count"]) for value in windows)
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    regime_contribution = {name: 0.0 for name in REGIME_NAMES.values()}
    cash_contribution = 0.0
    for value in windows:
        cash_contribution += float(value["standard"]["cash_contribution"])
        for asset, contribution in value["standard"]["asset_contribution"].items():
            asset_contribution[asset] += float(contribution)
        for regime, contribution in value["standard"]["regime_contribution"].items():
            regime_contribution[regime] += float(contribution)
    asset_share = positive_share(list(asset_contribution.values()))
    window_share = positive_share(standard_returns)
    regime_share = positive_share(list(regime_contribution.values()))
    gates = {
        "five_positive_standard_windows": all(value > 0.0 for value in standard_returns),
        "four_positive_stress_windows": sum(value > 0.0 for value in stress_returns) >= 4,
        "annualized_standard_at_least_five_percent": annualized >= 0.05,
        "aggregate_stress_positive": aggregate_stress > 0.0,
        "drawdown_cap": maximum_drawdown <= 0.10,
        "twenty_costed_actions": actions >= 20,
        "asset_diversity": "BTC" in selected_assets and len(set(selected_assets) - {"BTC"}) >= 2,
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
        "gated_assets": gated_assets,
        "gated_decision_count": gated_decisions,
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
        "never_added_asset": all(
            bool(value["standard"]["never_added_asset"])
            and bool(value["stress"]["never_added_asset"])
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
    macro_history: MacroHistory | None = None,
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
        raise MacroLiquidityStateV47Error("source report is unavailable")
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
        macro_history = load_macro_history()
    macro_matrix, macro_by_date = build_macro_matrix(dataset, macro_history)
    if len(macro_matrix) != len(dataset.X):
        raise MacroLiquidityStateV47Error("macro matrix row count mismatch")

    folds = v46.build_walk_forward_folds(dataset, cash_history)
    family_results: dict[str, list[FamilyFoldResult]] = {}
    for family in sorted(FAMILY_COLUMNS):
        family_results[family] = [
            fit_and_evaluate_family_fold(
                dataset,
                macro_by_date,
                cash_history,
                fold,
                family,
            )
            for fold in folds
        ]
    selected_family, selection = select_macro_family(family_results)
    probabilities, threshold, final_calibration = fit_final_family(
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
        probabilities,
        threshold,
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
            "feature_names": MACRO_FEATURE_NAMES,
            "families": {
                name: [MACRO_FEATURE_NAMES[index] for index in columns]
                for name, columns in FAMILY_COLUMNS.items()
            },
            "row_count": len(macro_matrix),
            "date_count": len(macro_by_date),
            "availability_rule": "newest observation dated <= decision date - 1 day",
        },
        "protocol_sha256": file_sha256(PROTOCOL_PATH),
        "implementation_contract_sha256": file_sha256(CONTRACT_PATH),
        "v46_protocol_sha256": file_sha256(v46.PROTOCOL_PATH),
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
            "macro_family_count": len(FAMILY_COLUMNS),
            "final_v43_retrained_for_v47": False,
        },
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run v4.7 macro-liquidity state paper research"
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
        "gated_decision_count": evaluation["gated_decision_count"],
        "report_sha256": report["report_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
