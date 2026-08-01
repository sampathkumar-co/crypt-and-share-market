from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
    build_dataset,
    file_sha256,
    positive_share,
    state_arrays,
)
from tradebot.research.regime_ranking_v42_sources import (
    canonical_json,
    load_all_sources,
    utc_iso,
)

SCHEMA_VERSION = "4.7-residual-momentum-breakout"
PROTOCOL_PATH = Path("research/V47_RESIDUAL_MOMENTUM_BREAKOUT_PROTOCOL.md")
CONTRACT_PATH = Path(
    "research/V471_RESIDUAL_MOMENTUM_BREAKOUT_IMPLEMENTATION_CONTRACT.md"
)
FOLD_WINDOWS = (
    ("fold-1", v43.day("2024-04-01"), v43.day("2024-06-30")),
    ("fold-2", v43.day("2024-07-01"), v43.day("2024-09-30")),
    ("fold-3", v43.day("2024-10-01"), v43.day("2024-12-31")),
    ("fold-4", v43.day("2025-01-01"), v43.day("2025-03-31")),
    ("fold-5", v43.day("2025-04-01"), v43.day("2025-06-30")),
    ("fold-6", v43.day("2025-07-01"), v43.day("2025-09-30")),
)
SIGNAL_NAMES = ("continuation", "breakout")


class ResidualMomentumBreakoutV47Error(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    residual_floor: float
    rank_floor: float
    efficiency_floor: float
    compression_ceiling: float
    breakout_buffer: float
    entry_mode: str


@dataclass
class ResidualDataset:
    dates: list[datetime]
    return1: np.ndarray
    residual20: np.ndarray
    residual60: np.ndarray
    residual_score: np.ndarray
    residual_rank: np.ndarray
    return7: np.ndarray
    return20: np.ndarray
    return60: np.ndarray
    return120: np.ndarray
    sma20_distance: np.ndarray
    sma50_distance: np.ndarray
    efficiency20: np.ndarray
    compression_ratio: np.ndarray
    breakout_distance20: np.ndarray
    volume_ratio20: np.ndarray
    btc_above_sma100: np.ndarray
    breadth50: np.ndarray
    observable_regime: np.ndarray


def safe_std(values: np.ndarray) -> float:
    return max(float(np.std(values)), 1e-9)


def path_efficiency(values: np.ndarray) -> float:
    movement = float(np.sum(np.abs(np.diff(values))))
    return abs(float(values[-1] - values[0])) / max(movement, 1e-12)


def stable_percentile(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    result = np.empty(len(values), dtype=float)
    result[order] = np.arange(len(values), dtype=float)
    return result / max(len(values) - 1, 1)


def factor_log_returns(log_returns: np.ndarray, asset_index: int) -> np.ndarray:
    if asset_index == 0:
        return np.mean(log_returns[1:], axis=0)
    return log_returns[0]


def beta60(asset_returns: np.ndarray, factor_returns: np.ndarray) -> float:
    variance = float(np.var(factor_returns))
    if variance <= 1e-12:
        return 0.0
    return float(np.cov(asset_returns, factor_returns, ddof=0)[0, 1]) / variance


def _compound_log(values: np.ndarray) -> float:
    return math.exp(float(np.sum(values))) - 1.0


def build_residual_dataset(states: dict[str, Any]) -> ResidualDataset:
    dates, arrays = state_arrays(states)
    if len(dates) < 123:
        raise ResidualMomentumBreakoutV47Error(
            "insufficient complete common dates for 120-day indicators"
        )
    opens = np.vstack([arrays[asset]["spot_open"] for asset in ASSETS])
    highs = np.vstack([arrays[asset]["spot_high"] for asset in ASSETS])
    closes = np.vstack([arrays[asset]["spot_close"] for asset in ASSETS])
    volumes = np.vstack([arrays[asset]["spot_volume"] for asset in ASSETS])
    for name, values in {
        "opens": opens,
        "highs": highs,
        "closes": closes,
        "volumes": volumes,
    }.items():
        if not np.all(np.isfinite(values)):
            raise ResidualMomentumBreakoutV47Error(f"non-finite {name}")
    if np.any(opens <= 0.0) or np.any(highs <= 0.0) or np.any(closes <= 0.0):
        raise ResidualMomentumBreakoutV47Error("non-positive OHLC prices")
    if np.any(volumes < 0.0):
        raise ResidualMomentumBreakoutV47Error("negative volume")

    log_returns = np.diff(
        np.log(closes), axis=1, prepend=np.log(closes[:, :1])
    )
    row_dates: list[datetime] = []
    fields: dict[str, list[np.ndarray]] = {
        name: []
        for name in (
            "return1",
            "residual20",
            "residual60",
            "residual_score",
            "residual_rank",
            "return7",
            "return20",
            "return60",
            "return120",
            "sma20_distance",
            "sma50_distance",
            "efficiency20",
            "compression_ratio",
            "breakout_distance20",
            "volume_ratio20",
        )
    }
    btc_above_sma100: list[float] = []
    breadth50: list[float] = []
    observable_regime: list[int] = []

    for index in range(120, len(dates) - 2):
        if (dates[index] - dates[index - 120]).days != 120:
            continue
        if (dates[index + 2] - dates[index]).days != 2:
            continue

        returns = {
            horizon: closes[:, index] / closes[:, index - horizon] - 1.0
            for horizon in (7, 20, 60, 120)
        }
        sma20 = np.mean(closes[:, index - 19:index + 1], axis=1)
        sma50 = np.mean(closes[:, index - 49:index + 1], axis=1)
        residual20 = np.zeros(len(ASSETS), dtype=float)
        residual60 = np.zeros(len(ASSETS), dtype=float)
        residual_score = np.zeros(len(ASSETS), dtype=float)
        efficiency20 = np.zeros(len(ASSETS), dtype=float)
        compression = np.zeros(len(ASSETS), dtype=float)
        breakout_distance = np.zeros(len(ASSETS), dtype=float)
        volume_ratio = np.zeros(len(ASSETS), dtype=float)

        for asset_index in range(len(ASSETS)):
            factor = factor_log_returns(log_returns, asset_index)
            beta = beta60(
                log_returns[asset_index, index - 59:index + 1],
                factor[index - 59:index + 1],
            )
            factor20 = _compound_log(factor[index - 19:index + 1])
            factor60 = _compound_log(factor[index - 59:index + 1])
            residual20[asset_index] = float(returns[20][asset_index]) - beta * factor20
            residual60[asset_index] = float(returns[60][asset_index]) - beta * factor60
            vol60 = safe_std(
                log_returns[asset_index, index - 59:index + 1]
            )
            residual_score[asset_index] = (
                0.5 * (residual20[asset_index] + residual60[asset_index])
                / vol60
            )
            efficiency20[asset_index] = path_efficiency(
                closes[asset_index, index - 19:index + 1]
            )
            vol10 = safe_std(
                log_returns[asset_index, index - 9:index + 1]
            )
            compression[asset_index] = vol10 / vol60
            prior_high = float(
                np.max(highs[asset_index, index - 20:index])
            )
            breakout_distance[asset_index] = (
                closes[asset_index, index] / prior_high - 1.0
            )
            mean_volume = float(
                np.mean(volumes[asset_index, index - 20:index])
            )
            volume_ratio[asset_index] = (
                volumes[asset_index, index] / max(mean_volume, 1e-12)
            )

        rank = stable_percentile(residual_score)
        btc_sma100 = float(
            np.mean(closes[0, index - 99:index + 1])
        )
        btc_above = float(closes[0, index] > btc_sma100)
        breadth = float(np.mean(closes[:, index] > sma50))
        btc_return20 = float(returns[20][0])
        btc_return60 = float(returns[60][0])
        if btc_return20 <= -0.08 and breadth < 0.40:
            regime = 2
        elif btc_above and breadth >= 0.60:
            regime = 1
        elif btc_return60 < 0.0 and btc_return20 > 0.0:
            regime = 3
        else:
            regime = 0

        row_dates.append(dates[index])
        fields["return1"].append(
            opens[:, index + 2] / opens[:, index + 1] - 1.0
        )
        fields["residual20"].append(residual20)
        fields["residual60"].append(residual60)
        fields["residual_score"].append(residual_score)
        fields["residual_rank"].append(rank)
        fields["return7"].append(returns[7])
        fields["return20"].append(returns[20])
        fields["return60"].append(returns[60])
        fields["return120"].append(returns[120])
        fields["sma20_distance"].append(
            closes[:, index] / sma20 - 1.0
        )
        fields["sma50_distance"].append(
            closes[:, index] / sma50 - 1.0
        )
        fields["efficiency20"].append(efficiency20)
        fields["compression_ratio"].append(compression)
        fields["breakout_distance20"].append(breakout_distance)
        fields["volume_ratio20"].append(volume_ratio)
        btc_above_sma100.append(btc_above)
        breadth50.append(breadth)
        observable_regime.append(regime)

    if not row_dates:
        raise ResidualMomentumBreakoutV47Error(
            "no complete residual-momentum rows"
        )
    converted = {
        name: np.asarray(values, dtype=float)
        for name, values in fields.items()
    }
    if any(
        not np.all(np.isfinite(values))
        for values in converted.values()
    ):
        raise ResidualMomentumBreakoutV47Error(
            "non-finite residual feature matrix"
        )
    return ResidualDataset(
        dates=row_dates,
        **converted,
        btc_above_sma100=np.asarray(btc_above_sma100, dtype=float),
        breadth50=np.asarray(breadth50, dtype=float),
        observable_regime=np.asarray(observable_regime, dtype=int),
    )


def config_grid() -> list[Config]:
    return [
        Config(*values)
        for values in itertools.product(
            (0.00, 0.02, 0.04),
            (0.60, 0.80),
            (0.20, 0.35, 0.50),
            (0.60, 0.80, 1.00),
            (0.00, 0.01),
            ("continuation", "breakout", "either"),
        )
    ]


def date_mask(
    dataset: ResidualDataset,
    start: datetime,
    end: datetime,
) -> np.ndarray:
    stamps = np.asarray([int(value.timestamp()) for value in dataset.dates])
    return (
        (stamps >= int(start.timestamp()))
        & (stamps <= int(end.timestamp()))
    )


def market_risk_on(dataset: ResidualDataset, index: int) -> bool:
    return bool(
        dataset.btc_above_sma100[index] > 0.5
        or dataset.breadth50[index] >= 0.60
    )


def qualification(
    dataset: ResidualDataset,
    index: int,
    asset_index: int,
    config: Config,
) -> tuple[bool, str | None]:
    common = (
        dataset.residual60[index, asset_index] >= config.residual_floor
        and dataset.residual_rank[index, asset_index] >= config.rank_floor
    )
    continuation = bool(
        common
        and dataset.return20[index, asset_index] > 0.0
        and dataset.return60[index, asset_index] > 0.0
        and dataset.sma20_distance[index, asset_index] > 0.0
        and dataset.sma50_distance[index, asset_index] > 0.0
        and dataset.efficiency20[index, asset_index] >= config.efficiency_floor
    )
    breakout = bool(
        common
        and dataset.return60[index, asset_index] > 0.0
        and dataset.compression_ratio[index, asset_index]
        <= config.compression_ceiling
        and dataset.breakout_distance20[index, asset_index]
        >= config.breakout_buffer
        and dataset.volume_ratio20[index, asset_index] >= 1.0
    )
    if config.entry_mode == "continuation":
        return continuation, "continuation" if continuation else None
    if config.entry_mode == "breakout":
        return breakout, "breakout" if breakout else None
    if breakout:
        return True, "breakout"
    if continuation:
        return True, "continuation"
    return False, None


def decisions_by_date(
    dataset: ResidualDataset,
    mask: np.ndarray,
    config: Config,
) -> dict[datetime, dict[str, Any]]:
    result: dict[datetime, dict[str, Any]] = {}
    for index in np.flatnonzero(mask):
        candidates: list[tuple[float, float, str, int, str]] = []
        if market_risk_on(dataset, int(index)):
            for asset_index, asset in enumerate(ASSETS):
                qualifies, signal = qualification(
                    dataset,
                    int(index),
                    asset_index,
                    config,
                )
                if qualifies and signal is not None:
                    candidates.append(
                        (
                            float(dataset.residual_score[index, asset_index]),
                            float(dataset.residual60[index, asset_index]),
                            asset,
                            asset_index,
                            signal,
                        )
                    )
        ordered = sorted(
            candidates,
            key=lambda item: (-item[0], -item[1], item[2]),
        )
        result[dataset.dates[int(index)]] = {
            "selected": [ordered[0][3]] if ordered else [],
            "signal": ordered[0][4] if ordered else None,
            "regime": int(dataset.observable_regime[int(index)]),
            "risk_on": market_risk_on(dataset, int(index)),
        }
    return result


def simulate(
    dataset: ResidualDataset,
    mask: np.ndarray,
    history: v44.CashRateHistory,
    config: Config,
    *,
    one_way_cost: float,
) -> dict[str, Any]:
    decisions = decisions_by_date(dataset, mask, config)
    index_by_date = {
        dataset.dates[index]: int(index)
        for index in np.flatnonzero(mask)
    }
    cash = 1.0
    holdings = {asset: 0.0 for asset in ASSETS}
    selected_asset: str | None = None
    holding_signal: str | None = None
    holding_regime = 0
    selected_ever: set[str] = set()
    peak = 1.0
    maximum_drawdown = 0.0
    turnover = 0.0
    actions = 0
    age = 3
    cash_contribution = 0.0
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    signal_contribution = {name: 0.0 for name in SIGNAL_NAMES}
    regime_contribution = {
        name: 0.0 for name in REGIME_NAMES.values()
    }
    signal_count = {name: 0 for name in SIGNAL_NAMES}
    maximum_target_exposure = 0.0
    maximum_gross_exposure = 0.0
    daily_returns: list[float] = []

    for stamp in sorted(decisions):
        index = index_by_date[stamp]
        equity_before = cash + sum(holdings.values())
        if age >= 3:
            decision = decisions[stamp]
            target_asset = (
                ASSETS[decision["selected"][0]]
                if decision["selected"]
                else None
            )
            target_values = {
                asset: (
                    0.05 * equity_before
                    if asset == target_asset
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
            if traded > 1e-12:
                cash -= one_way_cost * traded
                turnover += traded
                actions += 1
            cash += sum(
                holdings[asset] - target_values[asset]
                for asset in ASSETS
            )
            holdings = target_values
            selected_asset = target_asset
            holding_signal = decision["signal"] if target_asset else None
            holding_regime = int(decision["regime"])
            if target_asset:
                selected_ever.add(target_asset)
                if holding_signal:
                    signal_count[holding_signal] += 1
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

        if selected_asset is not None and holdings[selected_asset] > 0.0:
            asset_index = ASSETS.index(selected_asset)
            asset_return = float(dataset.return1[index, asset_index])
            contribution = holdings[selected_asset] * asset_return
            holdings[selected_asset] *= 1.0 + asset_return
            asset_contribution[selected_asset] += contribution
            if holding_signal:
                signal_contribution[holding_signal] += contribution
            regime_contribution[
                REGIME_NAMES[holding_regime]
            ] += contribution

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
        "target_changing_actions": actions,
        "selected_assets": sorted(selected_ever),
        "asset_contribution": asset_contribution,
        "signal_contribution": signal_contribution,
        "regime_contribution": regime_contribution,
        "signal_count": signal_count,
        "cash_contribution": cash_contribution,
        "maximum_target_exposure": maximum_target_exposure,
        "maximum_gross_exposure": maximum_gross_exposure,
        "daily_returns": daily_returns,
        "decision_count": len(decisions),
    }


def compound(values: list[float]) -> float:
    return float(np.prod([1.0 + value for value in values]) - 1.0)


def candidate_diagnostics(
    folds: list[dict[str, Any]],
) -> dict[str, Any]:
    standard_returns = [
        float(fold["standard"]["net_return"]) for fold in folds
    ]
    stress_returns = [
        float(fold["stress"]["net_return"]) for fold in folds
    ]
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    selected_assets: set[str] = set()
    actions = 0
    turnover = 0.0
    maximum_drawdown = 0.0
    for fold in folds:
        selected_assets.update(fold["standard"]["selected_assets"])
        actions += int(fold["standard"]["target_changing_actions"])
        turnover += float(fold["standard"]["turnover"])
        maximum_drawdown = max(
            maximum_drawdown,
            float(fold["standard"]["maximum_drawdown"]),
            float(fold["stress"]["maximum_drawdown"]),
        )
        for asset, contribution in fold["standard"][
            "asset_contribution"
        ].items():
            asset_contribution[asset] += float(contribution)
    asset_share = positive_share(list(asset_contribution.values()))
    fold_share = positive_share(standard_returns)
    eligible = (
        sum(value > 0.0 for value in standard_returns) >= 4
        and sum(value > 0.0 for value in stress_returns) >= 4
        and compound(standard_returns) > 0.0
        and compound(stress_returns) > 0.0
        and actions >= 20
        and "BTC" in selected_assets
        and len(selected_assets - {"BTC"}) >= 2
        and asset_share <= 0.70
        and fold_share <= 0.70
    )
    return {
        "eligible": eligible,
        "standard_returns": standard_returns,
        "stress_returns": stress_returns,
        "positive_standard_fold_count": sum(
            value > 0.0 for value in standard_returns
        ),
        "positive_stress_fold_count": sum(
            value > 0.0 for value in stress_returns
        ),
        "compounded_standard_return": compound(standard_returns),
        "compounded_stress_return": compound(stress_returns),
        "worst_standard_return": min(standard_returns),
        "worst_stress_return": min(stress_returns),
        "maximum_drawdown": maximum_drawdown,
        "turnover": turnover,
        "target_changing_actions": actions,
        "selected_assets": sorted(selected_assets),
        "asset_contribution": asset_contribution,
        "maximum_positive_asset_share": asset_share,
        "maximum_positive_fold_share": fold_share,
    }


def selection_key(
    diagnostic: dict[str, Any],
    config: Config,
) -> tuple[float, ...]:
    return (
        float(diagnostic["worst_standard_return"]),
        float(diagnostic["worst_stress_return"]),
        float(diagnostic["positive_standard_fold_count"]),
        float(diagnostic["positive_stress_fold_count"]),
        float(diagnostic["compounded_stress_return"]),
        float(diagnostic["compounded_standard_return"]),
        -float(diagnostic["maximum_drawdown"]),
        -float(diagnostic["turnover"]),
        config.residual_floor,
        config.rank_floor,
        config.efficiency_floor,
        -config.compression_ceiling,
        config.breakout_buffer,
        -float(
            {"continuation": 0, "breakout": 1, "either": 2}[
                config.entry_mode
            ]
        ),
    )


def select_config(
    dataset: ResidualDataset,
    history: v44.CashRateHistory,
) -> tuple[Config, dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    best_eligible: tuple[
        tuple[float, ...], Config, list[dict[str, Any]], dict[str, Any]
    ] | None = None
    best_diagnostic: tuple[
        tuple[float, ...], Config, list[dict[str, Any]], dict[str, Any]
    ] | None = None

    for config in config_grid():
        folds: list[dict[str, Any]] = []
        for name, start, end in FOLD_WINDOWS:
            mask = date_mask(dataset, start, end)
            standard = simulate(
                dataset,
                mask,
                history,
                config,
                one_way_cost=STANDARD_ONE_WAY_COST,
            )
            stress = simulate(
                dataset,
                mask,
                history,
                config,
                one_way_cost=STRESS_ONE_WAY_COST,
            )
            folds.append(
                {
                    "name": name,
                    "start": utc_iso(start),
                    "end": utc_iso(end),
                    "standard": standard,
                    "stress": stress,
                }
            )
        diagnostic = candidate_diagnostics(folds)
        key = selection_key(diagnostic, config)
        candidates.append(
            {
                "config": asdict(config),
                **{
                    key_name: diagnostic[key_name]
                    for key_name in (
                        "eligible",
                        "positive_standard_fold_count",
                        "positive_stress_fold_count",
                        "compounded_standard_return",
                        "compounded_stress_return",
                        "worst_standard_return",
                        "worst_stress_return",
                        "maximum_drawdown",
                        "turnover",
                        "target_changing_actions",
                        "selected_assets",
                        "maximum_positive_asset_share",
                        "maximum_positive_fold_share",
                    )
                },
            }
        )
        record = (key, config, folds, diagnostic)
        if best_diagnostic is None or key > best_diagnostic[0]:
            best_diagnostic = record
        if diagnostic["eligible"] and (
            best_eligible is None or key > best_eligible[0]
        ):
            best_eligible = record

    selected = best_eligible or best_diagnostic
    if selected is None:
        raise ResidualMomentumBreakoutV47Error(
            "configuration grid unexpectedly empty"
        )
    return selected[1], {
        "selected_config": asdict(selected[1]),
        "selected_eligible": bool(selected[3]["eligible"]),
        "selected_key": list(selected[0]),
        "selected_folds": selected[2],
        "selected_diagnostics": selected[3],
        "candidate_count": len(candidates),
        "eligible_candidate_count": sum(
            candidate["eligible"] for candidate in candidates
        ),
        "candidates": candidates,
    }


def _daily_correlation(
    left: list[float],
    right: list[float],
) -> float:
    size = min(len(left), len(right))
    if size < 2:
        return 0.0
    left_values = np.asarray(left[:size], dtype=float)
    right_values = np.asarray(right[:size], dtype=float)
    if safe_std(left_values) <= 1e-9 or safe_std(right_values) <= 1e-9:
        return 0.0
    value = float(np.corrcoef(left_values, right_values)[0, 1])
    return value if math.isfinite(value) else 0.0


def evaluate_sealed(
    dataset: ResidualDataset,
    history: v44.CashRateHistory,
    config: Config,
    *,
    v44_baseline: dict[str, Any],
) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    correlation_pairs: list[tuple[list[float], list[float]]] = []
    for position, (name, start, end) in enumerate(v43.SEALED_WINDOWS):
        mask = date_mask(dataset, start, end)
        standard = simulate(
            dataset,
            mask,
            history,
            config,
            one_way_cost=STANDARD_ONE_WAY_COST,
        )
        stress = simulate(
            dataset,
            mask,
            history,
            config,
            one_way_cost=STRESS_ONE_WAY_COST,
        )
        days = int(np.sum(mask))
        standard["verification_days"] = days
        stress["verification_days"] = days
        baseline_window = v44_baseline["windows"][position]["standard"]
        correlation_pairs.append(
            (
                list(standard["daily_returns"]),
                list(baseline_window.get("daily_returns", [])),
            )
        )
        windows.append(
            {
                "name": name,
                "start": utc_iso(start),
                "end": utc_iso(end),
                "standard": standard,
                "stress": stress,
            }
        )

    standard_returns = [
        float(window["standard"]["net_return"]) for window in windows
    ]
    stress_returns = [
        float(window["stress"]["net_return"]) for window in windows
    ]
    aggregate_standard = compound(standard_returns)
    aggregate_stress = compound(stress_returns)
    verification_days = sum(
        int(window["standard"]["verification_days"])
        for window in windows
    )
    annualized = (
        (1.0 + aggregate_standard) ** (365.0 / verification_days) - 1.0
        if verification_days > 0 and aggregate_standard > -1.0
        else -1.0
    )
    maximum_drawdown = max(
        max(
            float(window["standard"]["maximum_drawdown"]),
            float(window["stress"]["maximum_drawdown"]),
        )
        for window in windows
    )
    actions = sum(
        int(window["standard"]["target_changing_actions"])
        for window in windows
    )
    selected_assets = sorted(
        set().union(
            *[
                set(window["standard"]["selected_assets"])
                for window in windows
            ]
        )
    )
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    signal_contribution = {name: 0.0 for name in SIGNAL_NAMES}
    regime_contribution = {
        name: 0.0 for name in REGIME_NAMES.values()
    }
    cash_contribution = 0.0
    for window in windows:
        summary = window["standard"]
        cash_contribution += float(summary["cash_contribution"])
        for asset, contribution in summary[
            "asset_contribution"
        ].items():
            asset_contribution[asset] += float(contribution)
        for signal, contribution in summary[
            "signal_contribution"
        ].items():
            signal_contribution[signal] += float(contribution)
        for regime, contribution in summary[
            "regime_contribution"
        ].items():
            regime_contribution[regime] += float(contribution)

    asset_share = positive_share(list(asset_contribution.values()))
    window_share = positive_share(standard_returns)
    regime_share = positive_share(list(regime_contribution.values()))
    signal_share = positive_share(list(signal_contribution.values()))
    correlations = [
        _daily_correlation(left, right)
        for left, right in correlation_pairs
    ]
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
        if key
        not in {
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
        "signal_contribution": signal_contribution,
        "regime_contribution": regime_contribution,
        "cash_contribution": cash_contribution,
        "maximum_positive_asset_share": asset_share,
        "maximum_positive_window_share": window_share,
        "maximum_positive_regime_share": regime_share,
        "maximum_positive_signal_share": signal_share,
        "standard_window_returns": standard_returns,
        "stress_window_returns": stress_returns,
        "v44_daily_return_correlations": correlations,
        "mean_v44_daily_return_correlation": float(np.mean(correlations)),
        "gates": gates,
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
        raise ResidualMomentumBreakoutV47Error(
            "current source report unavailable"
        )
    if canonical_json(source_report) != canonical_json(
        baseline_report["source"]
    ):
        raise ResidualMomentumBreakoutV47Error(
            "source inventory differs from frozen v4.3"
        )
    reference_dataset = build_dataset(states)
    observed_dataset = {
        "row_count": len(reference_dataset.X),
        "date_count": len(set(reference_dataset.dates)),
        "first_date": utc_iso(min(reference_dataset.dates)),
        "last_date": utc_iso(max(reference_dataset.dates)),
        "feature_count": len(reference_dataset.feature_names),
        "training_end": utc_iso(v43.TRAIN_END),
        "calibration_start": utc_iso(v43.CALIBRATION_START),
        "calibration_end": utc_iso(v43.CALIBRATION_END),
    }
    if canonical_json(observed_dataset) != canonical_json(
        baseline_report["dataset"]
    ):
        raise ResidualMomentumBreakoutV47Error(
            "reference dataset metadata differs from frozen v4.3"
        )
    if canonical_json(v43.bundle_summary(final_bundle)) != canonical_json(
        baseline_report["bundle"]
    ):
        raise ResidualMomentumBreakoutV47Error(
            "final bundle differs from frozen v4.3"
        )
    reproduced_v43 = v43.evaluate_sealed(
        reference_dataset,
        final_bundle,
    )
    if canonical_json(reproduced_v43) != canonical_json(
        baseline_report["evaluation"]
    ):
        raise ResidualMomentumBreakoutV47Error(
            "final bundle does not reproduce frozen v4.3"
        )
    if cash_history is None:
        cash_history = v44.load_cash_history()
    residual_dataset = build_residual_dataset(states)
    if min(cash_history.annual_rates) > min(residual_dataset.dates):
        raise ResidualMomentumBreakoutV47Error(
            "cash history starts after residual dataset"
        )

    selected_config, selection = select_config(
        residual_dataset,
        cash_history,
    )
    v44_baseline = v44.evaluate_sealed(
        reference_dataset,
        final_bundle,
        cash_history,
        baseline=reproduced_v43,
    )
    evaluation = evaluate_sealed(
        residual_dataset,
        cash_history,
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
        "runtime": v44.runtime_versions(),
        "reference_dataset": observed_dataset,
        "residual_dataset": {
            "date_count": len(residual_dataset.dates),
            "first_date": utc_iso(min(residual_dataset.dates)),
            "last_date": utc_iso(max(residual_dataset.dates)),
            "asset_count": len(ASSETS),
        },
        "protocol_sha256": file_sha256(PROTOCOL_PATH),
        "implementation_contract_sha256": file_sha256(CONTRACT_PATH),
        "implementation_sha256": file_sha256(Path(__file__).resolve()),
        "baseline_report_sha256": baseline_report["report_sha256"],
        "baseline_bundle_sha256": baseline_bundle_sha256,
        "reproduction": {
            "source_inventory_exact": True,
            "dataset_metadata_exact": True,
            "bundle_summary_exact": True,
            "v43_evaluation_exact": True,
            "v43_retrained_for_v47": False,
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
        description="Run v4.7 residual momentum and breakout research"
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
    print(
        json.dumps(
            {
                "status": evaluation["status"],
                "report_sha256": report["report_sha256"],
                "selected_config": report["selection"][
                    "selected_config"
                ],
                "selected_eligible": report["selection"][
                    "selected_eligible"
                ],
                "eligible_candidate_count": report["selection"][
                    "eligible_candidate_count"
                ],
                "standard_return": evaluation[
                    "aggregate_standard_return"
                ],
                "stress_return": evaluation[
                    "aggregate_stress_return"
                ],
                "annualized_standard_return": evaluation[
                    "annualized_standard_return"
                ],
                "maximum_drawdown": evaluation["maximum_drawdown"],
                "mean_v44_daily_return_correlation": evaluation[
                    "mean_v44_daily_return_correlation"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
