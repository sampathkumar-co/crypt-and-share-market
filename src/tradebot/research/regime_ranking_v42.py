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

from tradebot.research.regime_ranking_v42_sources import (
    ASSETS,
    DailyAssetState,
    canonical_json,
    load_all_sources,
    utc_iso,
)

STANDARD_ONE_WAY_COST = 0.001
STRESS_ONE_WAY_COST = 0.002
PROTOCOL_PATH = Path("research/V42_REGIME_SPECIALIST_CROSS_ASSET_RANKING_PROTOCOL.md")
CONTRACT_PATH = Path("research/V421_REGIME_RANKING_IMPLEMENTATION_CONTRACT.md")
STRUCTURE_PATH = Path("research/V422_REGIME_SPECIALIST_MODEL_STRUCTURE_ADDENDUM.md")
FEATURE_SEMANTICS_PATH = Path("research/V423_EXACT_FEATURE_SEMANTICS_ADDENDUM.md")
SOURCE_AVAILABILITY_PATH = Path("research/V424_SOURCE_AVAILABILITY_ADDENDUM.md")
SCHEMA_VERSION = "4.2-regime-specialist-ranking"
REGIME_NAMES = {0: "chop", 1: "trend", 2: "panic", 3: "recovery"}
LONG_REGIMES = (0, 1, 3)


class RegimeRankingV42Error(RuntimeError):
    pass


@dataclass(frozen=True)
class FoldSpec:
    name: str
    train_start: datetime
    train_end: datetime
    calibration_start: datetime
    calibration_end: datetime
    verification_start: datetime
    verification_end: datetime


@dataclass
class Dataset:
    X: np.ndarray
    return1: np.ndarray
    return3: np.ndarray
    return7: np.ndarray
    rank3: np.ndarray
    meta: np.ndarray
    downside3: np.ndarray
    regimes: np.ndarray
    dates: list[datetime]
    assets: list[str]
    feature_names: list[str]


@dataclass
class Specialist:
    return3_models: list[Any]
    return7_models: list[Any]
    rank_models: list[Any]


@dataclass
class Bundle:
    specialists: dict[int, Specialist]
    meta_models: list[Any]
    downside_models: list[Any]
    regime_models: list[Any]
    config: dict[str, Any]
    meta_threshold: float
    downside_limit: float
    top_n: int
    disagreement_threshold: float
    feature_names: list[str]


@dataclass
class FoldResult:
    name: str
    bundle: Bundle
    calibration: dict[str, Any]
    standard: dict[str, Any]
    stress: dict[str, Any]


def day(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


FOLDS = (
    FoldSpec("2023-Q3", day("2022-10-19"), day("2023-03-31"),
             day("2023-04-01"), day("2023-06-30"), day("2023-07-01"), day("2023-09-30")),
    FoldSpec("2024-Q1", day("2022-10-19"), day("2023-09-30"),
             day("2023-10-01"), day("2023-12-31"), day("2024-01-01"), day("2024-03-31")),
    FoldSpec("2024-Q3", day("2022-10-19"), day("2024-03-31"),
             day("2024-04-01"), day("2024-06-30"), day("2024-07-01"), day("2024-09-30")),
    FoldSpec("2025-Q1", day("2022-10-19"), day("2024-09-30"),
             day("2024-10-01"), day("2024-12-31"), day("2025-01-01"), day("2025-03-31")),
    FoldSpec("2025-Q3", day("2022-10-19"), day("2025-03-31"),
             day("2025-04-01"), day("2025-06-30"), day("2025-07-01"), day("2025-09-30")),
)


def safe_std(values: np.ndarray) -> float:
    return max(float(np.std(values)), 1e-9)


def efficiency(values: np.ndarray) -> float:
    movement = float(np.sum(np.abs(np.diff(values))))
    return abs(float(values[-1] - values[0])) / max(movement, 1e-12)


def rolling_corr(left: np.ndarray, right: np.ndarray) -> float:
    value = float(np.corrcoef(left, right)[0, 1])
    return value if math.isfinite(value) else 0.0


def percentile_ranks(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(len(values), dtype=float)
    return ranks / max(len(values) - 1, 1)


def model_grid() -> list[dict[str, Any]]:
    return [
        {"learning_rate": rate, "max_leaf_nodes": leaves, "max_iter": 120}
        for rate in (0.04, 0.08)
        for leaves in (15, 31)
    ]


def feature_names() -> list[str]:
    names: list[str] = []
    for prefix in ("spot", "perp"):
        names.extend(
            f"{prefix}_return_{days}"
            for days in (1, 3, 7, 14, 30, 60, 120)
        )
    names.extend(
        f"spot_minus_perp_{days}"
        for days in (1, 3, 7, 14, 30, 60, 120)
    )
    names.extend([
        "basis_annualized",
        *(f"basis_change_{days}" for days in (1, 3, 7, 30)),
        "funding",
        "funding_mean_3",
        "funding_mean_7",
        "funding_z_30",
        "funding_sign_persistence",
    ])
    names.extend(f"oi_change_{days}" for days in (1, 3, 7, 30))
    names.extend([
        "state_long_build",
        "state_short_build",
        "state_long_liquidation",
        "state_short_covering",
    ])
    names.extend(
        f"spot_volume_change_{days}" for days in (1, 7, 30)
    )
    names.extend(
        f"perp_volume_change_{days}" for days in (1, 7, 30)
    )
    names.extend([
        "spot_flow",
        "spot_flow_mean_3",
        "spot_flow_mean_7",
        "perp_flow",
        "perp_flow_mean_3",
        "perp_flow_mean_7",
        "flow_divergence",
    ])
    names.extend(f"volatility_{days}" for days in (7, 30, 90))
    names.extend(
        f"sma_distance_{days}" for days in (20, 50, 100, 200)
    )
    names.extend([
        "efficiency_14",
        "efficiency_60",
        "beta_30",
        "corr_30",
        "beta_90",
        "corr_90",
    ])
    names.extend([
        "rank_momentum",
        "rank_basis_change",
        "rank_funding",
        "rank_oi_change",
        "rank_volatility",
        "rank_flow",
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
    ])
    names.extend(f"asset_{asset}" for asset in ASSETS)
    return names


FEATURE_NAMES = feature_names()


def state_arrays(
    states: dict[str, dict[datetime, DailyAssetState]],
) -> tuple[list[datetime], dict[str, dict[str, np.ndarray]]]:
    common = set.intersection(*(set(states[asset]) for asset in ASSETS))
    dates = sorted(common)
    arrays: dict[str, dict[str, np.ndarray]] = {}
    for asset in ASSETS:
        rows = [states[asset][stamp] for stamp in dates]
        arrays[asset] = {
            "spot_open": np.asarray([row.spot.open for row in rows], dtype=float),
            "spot_high": np.asarray([row.spot.high for row in rows], dtype=float),
            "spot_low": np.asarray([row.spot.low for row in rows], dtype=float),
            "spot_close": np.asarray([row.spot.close for row in rows], dtype=float),
            "spot_volume": np.asarray(
                [row.spot.quote_volume for row in rows], dtype=float
            ),
            "spot_flow": np.asarray([row.spot_flow for row in rows], dtype=float),
            "perp_close": np.asarray([row.perp.close for row in rows], dtype=float),
            "perp_volume": np.asarray(
                [row.perp.quote_volume for row in rows], dtype=float
            ),
            "perp_flow": np.asarray([row.perp_flow for row in rows], dtype=float),
            "funding": np.asarray([row.funding for row in rows], dtype=float),
            "open_interest": np.asarray(
                [row.open_interest for row in rows], dtype=float
            ),
            "basis": np.asarray([row.basis for row in rows], dtype=float),
        }
    return dates, arrays


def build_dataset(
    states: dict[str, dict[datetime, DailyAssetState]],
) -> Dataset:
    dates, arrays = state_arrays(states)
    if len(dates) < 208:
        raise RegimeRankingV42Error("insufficient complete common dates")
    spot_closes = np.vstack([
        arrays[asset]["spot_close"] for asset in ASSETS
    ])
    perp_closes = np.vstack([
        arrays[asset]["perp_close"] for asset in ASSETS
    ])
    spot_opens = np.vstack([
        arrays[asset]["spot_open"] for asset in ASSETS
    ])
    spot_lows = np.vstack([
        arrays[asset]["spot_low"] for asset in ASSETS
    ])
    spot_log_returns = np.diff(
        np.log(spot_closes), axis=1, prepend=np.log(spot_closes[:, :1])
    )
    rows: list[list[float]] = []
    labels1: list[float] = []
    labels3: list[float] = []
    labels7: list[float] = []
    ranks3: list[float] = []
    metas: list[int] = []
    downsides: list[int] = []
    regimes: list[int] = []
    row_dates: list[datetime] = []
    row_assets: list[str] = []

    for index in range(199, len(dates) - 8):
        if dates[index] - dates[index - 199] != timedelta(days=199):
            continue
        if dates[index + 8] - dates[index] != timedelta(days=8):
            continue
        market_close = np.mean(spot_closes, axis=0)
        market_return7 = float(
            market_close[index] / market_close[index - 7] - 1.0
        )
        market_return30 = float(
            market_close[index] / market_close[index - 30] - 1.0
        )
        breadth20 = float(np.mean([
            spot_closes[pos, index]
            > np.mean(spot_closes[pos, index - 19:index + 1])
            for pos in range(len(ASSETS))
        ]))
        breadth100 = float(np.mean([
            spot_closes[pos, index]
            > np.mean(spot_closes[pos, index - 99:index + 1])
            for pos in range(len(ASSETS))
        ]))
        momentum30 = (
            spot_closes[:, index] / spot_closes[:, index - 30] - 1.0
        )
        dispersion30 = float(np.std(momentum30))
        future1 = (
            spot_opens[:, index + 2] / spot_opens[:, index + 1] - 1.0
        )
        future3 = (
            spot_opens[:, index + 4] / spot_opens[:, index + 1] - 1.0
        )
        future7 = (
            spot_opens[:, index + 8] / spot_opens[:, index + 1] - 1.0
        )
        future_market7 = float(np.mean(future7))
        market_entry = float(np.mean(spot_opens[:, index + 1]))
        market_path = np.mean(
            spot_lows[:, index + 1:index + 4], axis=0
        )
        market_draw3 = float(
            np.min(market_path / market_entry - 1.0)
        )
        if market_draw3 <= -0.03:
            regime = 2
        elif market_return30 < -0.08 and future_market7 > 0.0:
            regime = 3
        elif (
            market_return30 > 0.0
            and breadth100 >= 0.60
            and future_market7 > 0.0
        ):
            regime = 1
        else:
            regime = 0
        realized_rank3 = percentile_ranks(future3)
        top_two = set(np.argsort(future3, kind="mergesort")[-2:])
        basis_change7 = np.asarray([
            arrays[asset]["basis"][index]
            - arrays[asset]["basis"][index - 7]
            for asset in ASSETS
        ])
        funding_now = np.asarray([
            arrays[asset]["funding"][index] for asset in ASSETS
        ])
        oi_change7 = np.asarray([
            arrays[asset]["open_interest"][index]
            / arrays[asset]["open_interest"][index - 7]
            - 1.0
            for asset in ASSETS
        ])
        volatility30 = np.asarray([
            safe_std(spot_log_returns[pos, index - 29:index + 1])
            for pos in range(len(ASSETS))
        ])
        flow_now = np.asarray([
            arrays[asset]["spot_flow"][index] for asset in ASSETS
        ])
        rank_features = {
            "momentum": percentile_ranks(momentum30),
            "basis": percentile_ranks(basis_change7),
            "funding": percentile_ranks(funding_now),
            "oi": percentile_ranks(oi_change7),
            "volatility": percentile_ranks(volatility30),
            "flow": percentile_ranks(flow_now),
        }
        pairwise: list[float] = []
        for left in range(len(ASSETS)):
            for right in range(left + 1, len(ASSETS)):
                pairwise.append(rolling_corr(
                    spot_log_returns[left, index - 29:index + 1],
                    spot_log_returns[right, index - 29:index + 1],
                ))
        average_correlation30 = float(np.mean(pairwise))
        long_build = (momentum30 > 0.0) & (oi_change7 > 0.0)
        short_build = (momentum30 < 0.0) & (oi_change7 > 0.0)
        long_liquidation = (momentum30 < 0.0) & (oi_change7 < 0.0)
        short_covering = (momentum30 > 0.0) & (oi_change7 < 0.0)
        recovery_state = np.asarray([
            spot_closes[pos, index] / spot_closes[pos, index - 30] - 1.0
            < -0.08
            and spot_closes[pos, index] / spot_closes[pos, index - 7] - 1.0
            > 0.0
            for pos in range(len(ASSETS))
        ])
        market_features = [
            float(spot_closes[0, index] / spot_closes[0, index - 30] - 1.0),
            safe_std(spot_log_returns[0, index - 29:index + 1]),
            float(spot_closes[0, index] > np.mean(spot_closes[0, index - 99:index + 1])),
            market_return7,
            market_return30,
            breadth20,
            breadth100,
            dispersion30,
        ]
        market_features.extend([
            float(np.median(funding_now)),
            float(np.median(oi_change7)),
            average_correlation30,
            float(np.mean(long_build)),
            float(np.mean(long_liquidation)),
            float(np.mean(recovery_state)),
        ])

        for pos, asset in enumerate(ASSETS):
            values = arrays[asset]
            spot = values["spot_close"]
            perp = values["perp_close"]
            funding = values["funding"]
            open_interest = values["open_interest"]
            spot_volume = values["spot_volume"]
            perp_volume = values["perp_volume"]
            spot_returns = {
                days: float(spot[index] / spot[index - days] - 1.0)
                for days in (1, 3, 7, 14, 30, 60, 120)
            }
            perp_returns = {
                days: float(perp[index] / perp[index - days] - 1.0)
                for days in (1, 3, 7, 14, 30, 60, 120)
            }
            oi_changes = {
                days: float(
                    open_interest[index] / open_interest[index - days] - 1.0
                )
                for days in (1, 3, 7, 30)
            }
            funding_window30 = funding[index - 29:index + 1]
            funding_z30 = float(
                (funding_window30[-1] - np.mean(funding_window30))
                / safe_std(funding_window30)
            )
            sign_persistence = float(
                abs(np.mean(np.sign(funding_window30)))
            )
            beta_corr: list[float] = []
            for window in (30, 90):
                left = spot_log_returns[pos, index - window + 1:index + 1]
                right = spot_log_returns[0, index - window + 1:index + 1]
                covariance = float(np.cov(left, right, ddof=0)[0, 1])
                beta_corr.extend([
                    covariance / max(float(np.var(right)), 1e-12),
                    rolling_corr(left, right),
                ])
            state_values = [
                float(long_build[pos]),
                float(short_build[pos]),
                float(long_liquidation[pos]),
                float(short_covering[pos]),
            ]
            volume_features = [
                float(spot_volume[index] / spot_volume[index - days] - 1.0)
                for days in (1, 7, 30)
            ]
            volume_features.extend([
                float(perp_volume[index] / perp_volume[index - days] - 1.0)
                for days in (1, 7, 30)
            ])
            row = [
                *(spot_returns[days] for days in (1, 3, 7, 14, 30, 60, 120)),
                *(perp_returns[days] for days in (1, 3, 7, 14, 30, 60, 120)),
                *(spot_returns[days] - perp_returns[days]
                  for days in (1, 3, 7, 14, 30, 60, 120)),
                float(365.0 * values["basis"][index]),
                *(float(values["basis"][index] - values["basis"][index - days])
                  for days in (1, 3, 7, 30)),
                float(funding[index]),
                float(np.mean(funding[index - 2:index + 1])),
                float(np.mean(funding[index - 6:index + 1])),
                funding_z30,
                sign_persistence,
                *(oi_changes[days] for days in (1, 3, 7, 30)),
                *state_values,
                *volume_features,
                float(values["spot_flow"][index]),
                float(np.mean(values["spot_flow"][index - 2:index + 1])),
                float(np.mean(values["spot_flow"][index - 6:index + 1])),
                float(values["perp_flow"][index]),
                float(np.mean(values["perp_flow"][index - 2:index + 1])),
                float(np.mean(values["perp_flow"][index - 6:index + 1])),
                float(values["spot_flow"][index] - values["perp_flow"][index]),
            ]
            row.extend([
                safe_std(spot_log_returns[pos, index - 6:index + 1]),
                safe_std(spot_log_returns[pos, index - 29:index + 1]),
                safe_std(spot_log_returns[pos, index - 89:index + 1]),
                *(float(spot[index] / np.mean(spot[index - days + 1:index + 1]) - 1.0)
                  for days in (20, 50, 100, 200)),
                efficiency(spot[index - 13:index + 1]),
                efficiency(spot[index - 59:index + 1]),
                beta_corr[0],
                beta_corr[1],
                beta_corr[2],
                beta_corr[3],
                float(rank_features["momentum"][pos]),
                float(rank_features["basis"][pos]),
                float(rank_features["funding"][pos]),
                float(rank_features["oi"][pos]),
                float(rank_features["volatility"][pos]),
                float(rank_features["flow"][pos]),
                *market_features,
            ])
            row.extend(1.0 if asset == candidate else 0.0 for candidate in ASSETS)
            if len(row) != len(FEATURE_NAMES):
                raise RegimeRankingV42Error(
                    f"feature width mismatch: {len(row)} != {len(FEATURE_NAMES)}"
                )
            entry = float(spot_opens[pos, index + 1])
            path_loss = float(
                np.min(spot_lows[pos, index + 1:index + 4] / entry - 1.0)
            )
            rows.append(row)
            labels1.append(float(future1[pos]))
            labels3.append(float(future3[pos]))
            labels7.append(float(future7[pos]))
            ranks3.append(float(realized_rank3[pos]))
            metas.append(int(
                pos in top_two
                and float(future3[pos]) > 2.0 * STRESS_ONE_WAY_COST
            ))
            downsides.append(int(path_loss <= -0.02))
            regimes.append(regime)
            row_dates.append(dates[index])
            row_assets.append(asset)

    X = np.asarray(rows, dtype=float)
    if not len(X):
        raise RegimeRankingV42Error("no complete feature rows")
    if not np.all(np.isfinite(X)):
        raise RegimeRankingV42Error("nonfinite feature matrix")
    return Dataset(
        X=X,
        return1=np.asarray(labels1, dtype=float),
        return3=np.asarray(labels3, dtype=float),
        return7=np.asarray(labels7, dtype=float),
        rank3=np.asarray(ranks3, dtype=float),
        meta=np.asarray(metas, dtype=int),
        downside3=np.asarray(downsides, dtype=int),
        regimes=np.asarray(regimes, dtype=int),
        dates=row_dates,
        assets=row_assets,
        feature_names=FEATURE_NAMES,
    )


def fold_masks(
    dataset: Dataset,
    fold: FoldSpec,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    stamps = np.asarray([int(value.timestamp()) for value in dataset.dates])
    def between(start: datetime, end: datetime) -> np.ndarray:
        return (
            (stamps >= int(start.timestamp()))
            & (stamps <= int(end.timestamp()))
        )
    return (
        between(fold.train_start, fold.train_end),
        between(fold.calibration_start, fold.calibration_end),
        between(fold.verification_start, fold.verification_end),
    )


def _positive_probability(model: Any, X: np.ndarray) -> np.ndarray:
    probabilities = model.predict_proba(X)
    classes = list(model.classes_)
    if 1 not in classes:
        return np.zeros(len(X), dtype=float)
    return probabilities[:, classes.index(1)]


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


def _fit_specialist(
    dataset: Dataset,
    mask: np.ndarray,
    config: dict[str, Any],
) -> Specialist | None:
    from sklearn.ensemble import HistGradientBoostingRegressor

    indexes = np.flatnonzero(mask)
    if len(indexes) < 250:
        return None
    if len(np.unique(dataset.rank3[indexes])) < 2:
        return None

    def regress(target: np.ndarray, seeds: tuple[int, ...]) -> list[Any]:
        return [
            HistGradientBoostingRegressor(
                **config,
                l2_regularization=0.1,
                random_state=seed,
            ).fit(dataset.X[indexes], target[indexes])
            for seed in seeds
        ]

    return Specialist(
        return3_models=regress(dataset.return3, (17, 41, 83)),
        return7_models=regress(dataset.return7, (19, 43, 89)),
        rank_models=regress(dataset.rank3, (23, 47, 97)),
    )


def _majority_vote(matrix: np.ndarray) -> np.ndarray:
    result = np.zeros(matrix.shape[1], dtype=int)
    for index in range(matrix.shape[1]):
        values = matrix[:, index].astype(int)
        result[index] = int(np.bincount(values, minlength=4).argmax())
    return result


def predict_bundle(bundle: Bundle, X: np.ndarray) -> dict[str, Any]:
    meta_matrix = np.vstack([
        _positive_probability(model, X) for model in bundle.meta_models
    ])
    downside_matrix = np.vstack([
        _positive_probability(model, X) for model in bundle.downside_models
    ])
    regime_matrix = np.vstack([
        model.predict(X) for model in bundle.regime_models
    ])
    predictions: dict[str, Any] = {
        "meta": np.mean(meta_matrix, axis=0),
        "meta_std": np.std(meta_matrix, axis=0),
        "downside": np.mean(downside_matrix, axis=0),
        "downside_std": np.std(downside_matrix, axis=0),
        "regime": _majority_vote(regime_matrix),
        "specialists": {},
    }
    for regime, specialist in bundle.specialists.items():
        return3_matrix = np.vstack([
            model.predict(X) for model in specialist.return3_models
        ])
        return7_matrix = np.vstack([
            model.predict(X) for model in specialist.return7_models
        ])
        rank_matrix = np.vstack([
            model.predict(X) for model in specialist.rank_models
        ])
        disagreement = np.sqrt(
            np.std(return3_matrix, axis=0) ** 2
            + np.std(return7_matrix, axis=0) ** 2
            + np.std(rank_matrix, axis=0) ** 2
            + predictions["meta_std"] ** 2
            + predictions["downside_std"] ** 2
        )
        predictions["specialists"][regime] = {
            "return3": np.mean(return3_matrix, axis=0),
            "return7": np.mean(return7_matrix, axis=0),
            "rank": np.mean(rank_matrix, axis=0),
            "disagreement": disagreement,
        }
    return predictions


def row_disagreement(
    predictions: dict[str, Any],
) -> np.ndarray:
    values = np.full(len(predictions["regime"]), np.inf, dtype=float)
    for regime, specialist in predictions["specialists"].items():
        mask = predictions["regime"] == regime
        values[mask] = specialist["disagreement"][mask]
    return values


def decisions_by_date(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: Bundle,
    predictions: dict[str, Any],
) -> dict[datetime, dict[str, Any]]:
    grouped: dict[datetime, list[int]] = {}
    for index in np.flatnonzero(mask):
        grouped.setdefault(dataset.dates[int(index)], []).append(int(index))
    result: dict[datetime, dict[str, Any]] = {}
    for date in sorted(grouped):
        indexes = grouped[date]
        regime_values = [
            int(predictions["regime"][index]) for index in indexes
        ]
        regime = int(
            np.bincount(np.asarray(regime_values), minlength=4).argmax()
        )
        ranked: list[tuple[float, float, str, int]] = []
        specialist = predictions["specialists"].get(regime)
        if regime != 2 and specialist is not None:
            for index in indexes:
                return3 = float(specialist["return3"][index])
                return7 = float(specialist["return7"][index])
                meta = float(predictions["meta"][index])
                downside = float(predictions["downside"][index])
                disagreement = float(specialist["disagreement"][index])
                if return3 <= 2.0 * STRESS_ONE_WAY_COST:
                    continue
                if return7 <= 2.0 * STRESS_ONE_WAY_COST:
                    continue
                if meta < bundle.meta_threshold:
                    continue
                if downside > bundle.downside_limit:
                    continue
                if disagreement > bundle.disagreement_threshold:
                    continue
                score = (
                    0.60 * return3
                    + 0.40 * return7
                    + 0.25 * meta
                    - 0.35 * downside
                    - 0.50 * disagreement
                )
                ranked.append((
                    float(specialist["rank"][index]),
                    score,
                    dataset.assets[index],
                    index,
                ))
        ordered = sorted(
            ranked,
            key=lambda item: (-item[0], -item[1], item[2]),
        )
        selected = [item[3] for item in ordered[:bundle.top_n]]
        result[date] = {
            "regime": regime,
            "selected": selected,
            "candidate_count": len(ranked),
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

    for date in sorted(decisions):
        equity_before = cash + sum(holdings.values())
        decision = decisions[date]
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
            index = index_map[(date, asset)]
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


def fit_model_family(
    dataset: Dataset,
    train_mask: np.ndarray,
    config: dict[str, Any],
) -> Bundle:
    specialists: dict[int, Specialist] = {}
    for regime in LONG_REGIMES:
        specialist = _fit_specialist(
            dataset,
            train_mask & (dataset.regimes == regime),
            config,
        )
        if specialist is not None:
            specialists[regime] = specialist

    meta_models = [
        _fit_classifier(
            dataset.X[train_mask],
            dataset.meta[train_mask],
            config,
            seed,
        )
        for seed in (29, 53, 101)
    ]
    downside_models = [
        _fit_classifier(
            dataset.X[train_mask],
            dataset.downside3[train_mask],
            config,
            seed,
        )
        for seed in (31, 59, 103)
    ]
    regime_models = [
        _fit_classifier(
            dataset.X[train_mask],
            dataset.regimes[train_mask],
            config,
            seed,
        )
        for seed in (37, 61, 107)
    ]
    return Bundle(
        specialists=specialists,
        meta_models=meta_models,
        downside_models=downside_models,
        regime_models=regime_models,
        config=dict(config),
        meta_threshold=0.45,
        downside_limit=0.35,
        top_n=1,
        disagreement_threshold=float("inf"),
        feature_names=dataset.feature_names,
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
        -bundle.meta_threshold,
        -bundle.downside_limit,
        -float(bundle.top_n),
        -float(bundle.config["learning_rate"]),
        -float(bundle.config["max_leaf_nodes"]),
    )


def train_fold(
    dataset: Dataset,
    fold: FoldSpec,
) -> tuple[Bundle, dict[str, Any]]:
    train_mask, calibration_mask, _ = fold_masks(dataset, fold)
    if int(np.sum(train_mask)) < 500:
        raise RegimeRankingV42Error(
            f"{fold.name} has insufficient training rows"
        )
    if int(np.sum(calibration_mask)) < 50:
        raise RegimeRankingV42Error(
            f"{fold.name} has insufficient calibration rows"
        )
    best: tuple[tuple[float, ...], Bundle, dict[str, Any]] | None = None
    for config in model_grid():
        provisional = fit_model_family(dataset, train_mask, config)
        predictions = predict_bundle(provisional, dataset.X)
        disagreement = row_disagreement(predictions)
        finite = calibration_mask & np.isfinite(disagreement)
        threshold = (
            float(np.quantile(disagreement[finite], 0.75))
            if np.any(finite)
            else float("inf")
        )
        for meta_threshold in (0.45, 0.55, 0.65):
            for downside_limit in (0.35, 0.45):
                for top_n in (1, 2):
                    bundle = Bundle(
                        specialists=provisional.specialists,
                        meta_models=provisional.meta_models,
                        downside_models=provisional.downside_models,
                        regime_models=provisional.regime_models,
                        config=dict(config),
                        meta_threshold=meta_threshold,
                        downside_limit=downside_limit,
                        top_n=top_n,
                        disagreement_threshold=threshold,
                        feature_names=dataset.feature_names,
                    )
                    summary = simulate(
                        dataset,
                        calibration_mask,
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
                    key = calibration_tie_key(score, summary, bundle)
                    if best is None or key > best[0]:
                        best = (key, bundle, summary)
    if best is None:
        raise RegimeRankingV42Error(
            f"{fold.name} calibration produced no bundle"
        )
    return best[1], {
        "calibration_score": best[0][0],
        "calibration_summary": best[2],
        "selected_config": best[1].config,
        "meta_threshold": best[1].meta_threshold,
        "downside_limit": best[1].downside_limit,
        "top_n": best[1].top_n,
        "disagreement_threshold": best[1].disagreement_threshold,
        "available_specialists": [
            REGIME_NAMES[value]
            for value in sorted(best[1].specialists)
        ],
    }


def evaluate_fold(
    dataset: Dataset,
    fold: FoldSpec,
    bundle: Bundle,
    calibration: dict[str, Any],
) -> FoldResult:
    _, _, verification_mask = fold_masks(dataset, fold)
    if int(np.sum(verification_mask)) < 50:
        raise RegimeRankingV42Error(
            f"{fold.name} has insufficient verification rows"
        )
    predictions = predict_bundle(bundle, dataset.X)
    standard = simulate(
        dataset,
        verification_mask,
        bundle,
        predictions,
        one_way_cost=STANDARD_ONE_WAY_COST,
    )
    stress = simulate(
        dataset,
        verification_mask,
        bundle,
        predictions,
        one_way_cost=STRESS_ONE_WAY_COST,
    )
    verification_days = len({
        dataset.dates[index]
        for index in np.flatnonzero(verification_mask)
    })
    standard["verification_days"] = verification_days
    stress["verification_days"] = verification_days
    return FoldResult(
        name=fold.name,
        bundle=bundle,
        calibration=calibration,
        standard=standard,
        stress=stress,
    )


def positive_share(values: list[float]) -> float:
    positives = [max(0.0, value) for value in values]
    total = sum(positives)
    return max(positives) / total if total > 0.0 else 1.0


def aggregate_results(results: list[FoldResult]) -> dict[str, Any]:
    standard_returns = [
        float(result.standard["net_return"]) for result in results
    ]
    stress_returns = [
        float(result.stress["net_return"]) for result in results
    ]
    aggregate_standard = float(
        np.prod([1.0 + value for value in standard_returns]) - 1.0
    )
    aggregate_stress = float(
        np.prod([1.0 + value for value in stress_returns]) - 1.0
    )
    verification_days = sum(
        int(result.standard["verification_days"])
        for result in results
    )
    annualized = (
        (1.0 + aggregate_standard) ** (365.0 / verification_days) - 1.0
        if verification_days > 0 and aggregate_standard > -1.0
        else -1.0
    )
    maximum_drawdown = max(
        max(
            float(result.standard["maximum_drawdown"]),
            float(result.stress["maximum_drawdown"]),
        )
        for result in results
    )
    actions = sum(
        int(result.standard["target_changing_actions"])
        for result in results
    )
    selected_assets = sorted(set().union(*[
        set(result.standard["selected_assets"])
        for result in results
    ]))
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    regime_contribution = {
        name: 0.0 for name in REGIME_NAMES.values()
    }
    for result in results:
        for asset, value in result.standard["asset_contribution"].items():
            asset_contribution[asset] += float(value)
        for regime, value in result.standard["regime_contribution"].items():
            regime_contribution[regime] += float(value)
    asset_share = positive_share(list(asset_contribution.values()))
    fold_share = positive_share(standard_returns)
    regime_share = positive_share(list(regime_contribution.values()))
    gates = {
        "five_positive_standard_folds": all(
            value > 0.0 for value in standard_returns
        ),
        "five_positive_stress_folds": all(
            value > 0.0 for value in stress_returns
        ),
        "annualized_standard_at_least_five_percent": annualized >= 0.05,
        "aggregate_stress_positive": aggregate_stress > 0.0,
        "drawdown_cap": maximum_drawdown <= 0.10,
        "twenty_costed_actions": actions >= 20,
        "asset_diversity": (
            "BTC" in selected_assets
            and len(set(selected_assets) - {"BTC"}) >= 2
        ),
        "asset_concentration": asset_share <= 0.70,
        "fold_concentration": fold_share <= 0.70,
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
        "maximum_positive_fold_share": fold_share,
        "maximum_positive_regime_share": regime_share,
        "standard_fold_returns": standard_returns,
        "stress_fold_returns": stress_returns,
        "gates": gates,
        "status": (
            "HISTORICAL_BREAKTHROUGH_PENDING_REPLICATION_AND_SMOKE"
            if historical_only
            else "NOT_YET_HISTORICAL_BREAKTHROUGH"
        ),
    }


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bundle_summary(bundle: Bundle) -> dict[str, Any]:
    return {
        "config": bundle.config,
        "meta_threshold": bundle.meta_threshold,
        "downside_limit": bundle.downside_limit,
        "top_n": bundle.top_n,
        "disagreement_threshold": bundle.disagreement_threshold,
        "available_specialists": [
            REGIME_NAMES[value]
            for value in sorted(bundle.specialists)
        ],
    }


def run_campaign(
    states: dict[str, dict[datetime, DailyAssetState]] | None = None,
    source_report: dict[str, Any] | None = None,
    *,
    monthly_workers: int = 24,
    metrics_workers: int = 48,
) -> tuple[dict[str, Any], list[FoldResult]]:
    if states is None:
        states, source_report = load_all_sources(
            monthly_workers=monthly_workers,
            metrics_workers=metrics_workers,
        )
    if source_report is None:
        source_report = {"schema_version": "synthetic-v42-source"}
    dataset = build_dataset(states)
    results: list[FoldResult] = []
    for fold in FOLDS:
        bundle, calibration = train_fold(dataset, fold)
        results.append(evaluate_fold(
            dataset,
            fold,
            bundle,
            calibration,
        ))
    aggregate = aggregate_results(results)
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
        },
        "protocol_sha256": file_sha256(PROTOCOL_PATH),
        "implementation_contract_sha256": file_sha256(CONTRACT_PATH),
        "model_structure_sha256": file_sha256(STRUCTURE_PATH),
        "feature_semantics_sha256": file_sha256(FEATURE_SEMANTICS_PATH),
        "source_availability_sha256": file_sha256(SOURCE_AVAILABILITY_PATH),
        "folds": [
            {
                "name": result.name,
                "bundle": bundle_summary(result.bundle),
                "calibration": result.calibration,
                "standard": result.standard,
                "stress": result.stress,
            }
            for result in results
        ],
        "evaluation": aggregate,
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report, results


def save_bundles(
    path: Path,
    results: list[FoldResult],
) -> None:
    import joblib

    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "schema_version": SCHEMA_VERSION,
        "authorizes_trading": False,
        "authorizes_shadow_paper": True,
        "feature_names": FEATURE_NAMES,
        "folds": [
            {
                "name": result.name,
                "bundle": result.bundle,
            }
            for result in results
        ],
    }, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run frozen v4.2 regime-specialist paper research"
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("evidence/v42/historical.json"),
    )
    parser.add_argument(
        "--bundle-out",
        type=Path,
        default=Path("evidence/v42/bundles.joblib"),
    )
    parser.add_argument("--monthly-workers", type=int, default=24)
    parser.add_argument("--metrics-workers", type=int, default=48)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report, results = run_campaign(
        monthly_workers=max(1, args.monthly_workers),
        metrics_workers=max(1, args.metrics_workers),
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    save_bundles(args.bundle_out, results)
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
