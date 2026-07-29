from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from tradebot.backtest.metrics import (
    max_drawdown,
    period_returns,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
)
from tradebot.backtest.paper_trader import PaperTrader
from tradebot.backtest.profit_quality_gate import (
    STRATEGIES,
    ProfitQualityConfig,
    _cash_metrics,
    _candidate_grid,
    _evaluate_unseen,
    _fold_metrics,
    _gate_score_config,
)
from tradebot.backtest.research_gate import (
    EXECUTION_PROFILES,
    _candidate_score,
    _execution_config,
    dataset_fingerprint,
    independent_train_test_windows,
    load_histories,
)
from tradebot.backtest.selection_stability import (
    TemporalStabilityPolicy,
    stability_adjusted_score,
    stability_reasons,
    summarize_fold_metrics,
    with_stability_flag,
)
from tradebot.backtest.walk_forward import (
    DEFAULT_PARAMETER_GRIDS,
    build_strategy,
    result_metrics,
)
from tradebot.models import BacktestResult, Candle, Market


META_ALLOCATION_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class MetaAllocationConfig:
    train_size: int = 180
    test_size: int = 60
    max_candidates_per_strategy: int = 27
    stability_screen_candidates: int = 9
    ensemble_candidates: int = 3
    min_plateau_members: int = 2
    plateau_radius: float = 0.38
    min_consensus_strength: float = 0.55
    target_annual_volatility: float = 0.20
    min_total_exposure: float = 0.10
    max_total_exposure: float = 0.60
    min_cash_reserve: float = 0.40
    max_asset_exposure: float = 0.25
    max_pair_correlation: float = 0.80
    drawdown_brake_trigger: float = 0.08
    drawdown_brake_multiplier: float = 0.50
    drawdown_recovery_step: float = 0.15
    min_deployed_periods: int = 6
    min_positive_deployed_fraction: float = 0.50
    min_meta_beats_v05_fraction: float = 0.50
    min_average_improvement_vs_v05: float = 0.0
    max_unseen_drawdown: float = 0.20
    starting_cash: float = 100000.0
    stability_policy: TemporalStabilityPolicy = field(default_factory=TemporalStabilityPolicy)

    def __post_init__(self) -> None:
        if self.train_size < 30 or self.test_size < 10:
            raise ValueError("train_size must be >= 30 and test_size must be >= 10")
        if self.max_candidates_per_strategy < 1:
            raise ValueError("max_candidates_per_strategy must be positive")
        if not 1 <= self.stability_screen_candidates <= self.max_candidates_per_strategy:
            raise ValueError("stability_screen_candidates must be within the candidate budget")
        if not 1 <= self.ensemble_candidates <= self.stability_screen_candidates:
            raise ValueError("ensemble_candidates must be within the screening budget")
        if self.min_plateau_members < 2:
            raise ValueError("min_plateau_members must be at least two")
        if not 0 < self.plateau_radius <= 1:
            raise ValueError("plateau_radius must be between zero and one")
        if not 0 <= self.min_consensus_strength <= 1:
            raise ValueError("min_consensus_strength must be between zero and one")
        if not 0 < self.target_annual_volatility < 2:
            raise ValueError("target_annual_volatility must be positive")
        if not 0 <= self.min_total_exposure <= self.max_total_exposure <= 1:
            raise ValueError("total exposure bounds must be between zero and one")
        if not 0 <= self.min_cash_reserve < 1:
            raise ValueError("min_cash_reserve must be between zero and one")
        if self.max_total_exposure > 1.0 - self.min_cash_reserve + 1e-12:
            raise ValueError("max_total_exposure conflicts with min_cash_reserve")
        if not 0 < self.max_asset_exposure <= 1:
            raise ValueError("max_asset_exposure must be between zero and one")
        if not -1 <= self.max_pair_correlation <= 1:
            raise ValueError("max_pair_correlation must be between -1 and 1")
        if not 0 < self.drawdown_brake_trigger < 1:
            raise ValueError("drawdown_brake_trigger must be between zero and one")
        if not 0 < self.drawdown_brake_multiplier <= 1:
            raise ValueError("drawdown_brake_multiplier must be between zero and one")
        if not 0 <= self.drawdown_recovery_step <= 1:
            raise ValueError("drawdown_recovery_step must be between zero and one")
        if self.min_deployed_periods < 1:
            raise ValueError("min_deployed_periods must be positive")
        for value, name in (
            (self.min_positive_deployed_fraction, "min_positive_deployed_fraction"),
            (self.min_meta_beats_v05_fraction, "min_meta_beats_v05_fraction"),
        ):
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between zero and one")
        if not 0 < self.max_unseen_drawdown < 1:
            raise ValueError("max_unseen_drawdown must be between zero and one")
        if self.starting_cash <= 0:
            raise ValueError("starting_cash must be positive")


@dataclass
class MetaAllocationPeriod:
    symbol: str
    strategy: str
    period: int
    train_start: str
    train_end: str
    unseen_start: str
    unseen_end: str
    abstained: bool
    abstention_reasons: list[str]
    plateau_size: int
    ensemble_size: int
    consensus_strength: float
    annualized_training_volatility: float
    total_exposure: float
    candidate_allocations: list[dict[str, Any]]
    meta_metrics: dict[str, float | int]
    v05_metrics: dict[str, float | int]
    naive_metrics: dict[str, float | int]
    improvement_vs_v05: float
    improvement_vs_naive: float


@dataclass
class PortfolioPeriod:
    strategy: str
    period: int
    unseen_start: str
    unseen_end: str
    selected_symbols: list[str]
    correlation_rejections: list[str]
    asset_weights: dict[str, float]
    cash_weight: float
    risk_multiplier: float
    meta_metrics: dict[str, float | int]
    v05_metrics: dict[str, float | int]
    naive_metrics: dict[str, float | int]
    improvement_vs_v05: float
    improvement_vs_naive: float


@dataclass
class MetaStrategyResult:
    strategy: str
    approved: bool
    reasons: list[str]
    periods: list[MetaAllocationPeriod]
    portfolio_periods: list[PortfolioPeriod]
    deployed_periods: int
    abstained_periods: int
    average_meta_return: float
    average_v05_return: float
    average_naive_return: float
    average_improvement_vs_v05: float
    average_improvement_vs_naive: float
    positive_deployed_fraction: float
    meta_beats_v05_fraction: float
    worst_meta_drawdown: float
    portfolio_average_return: float
    portfolio_average_v05_return: float
    portfolio_average_improvement_vs_v05: float
    portfolio_positive_fraction: float
    portfolio_worst_drawdown: float


@dataclass
class MetaAllocationReport:
    schema_version: str
    generated_at: str
    market: str
    dataset_fingerprint: str
    symbols: list[str]
    config: dict[str, Any]
    strategies: list[MetaStrategyResult]
    accepted: bool
    champion_strategy: str | None
    reasons: list[str]
    paper_only: bool = True
    authorizes_real_trading: bool = False


@dataclass
class _PeriodEvidence:
    public: MetaAllocationPeriod
    train: list[Candle]
    meta_curve: list[float]
    v05_curve: list[float]
    naive_curve: list[float]


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def _annualized_volatility(candles: list[Candle], annualization: int = 365) -> float:
    returns = [
        candles[index].close / candles[index - 1].close - 1.0
        for index in range(1, len(candles))
        if candles[index - 1].close > 0
    ]
    if len(returns) < 2:
        return 0.0
    return pstdev(returns) * math.sqrt(annualization)


def _pearson(left: list[float], right: list[float]) -> float:
    size = min(len(left), len(right))
    if size < 3:
        return 0.0
    left = left[-size:]
    right = right[-size:]
    left_mean = mean(left)
    right_mean = mean(right)
    left_dev = [item - left_mean for item in left]
    right_dev = [item - right_mean for item in right]
    denominator = math.sqrt(
        sum(item * item for item in left_dev) * sum(item * item for item in right_dev)
    )
    if denominator <= 1e-15:
        return 0.0
    return _clamp(
        sum(a * b for a, b in zip(left_dev, right_dev)) / denominator,
        -1.0,
        1.0,
    )


def _close_returns(candles: list[Candle]) -> list[float]:
    return [
        candles[index].close / candles[index - 1].close - 1.0
        for index in range(1, len(candles))
        if candles[index - 1].close > 0
    ]


def _grid_index(strategy_name: str, key: str, value: Any) -> int:
    values = list(DEFAULT_PARAMETER_GRIDS[strategy_name][key])
    try:
        return values.index(value)
    except ValueError:
        return 0


def _execution_index(execution: dict[str, Any]) -> int:
    for index, profile in enumerate(EXECUTION_PROFILES):
        if dict(profile) == dict(execution):
            return index
    return 0


def parameter_distance(
    strategy_name: str,
    left_parameters: dict[str, Any],
    left_execution: dict[str, Any],
    right_parameters: dict[str, Any],
    right_execution: dict[str, Any],
) -> float:
    distances: list[float] = []
    for key, values in DEFAULT_PARAMETER_GRIDS[strategy_name].items():
        denominator = max(len(values) - 1, 1)
        distances.append(
            abs(
                _grid_index(strategy_name, key, left_parameters.get(key))
                - _grid_index(strategy_name, key, right_parameters.get(key))
            )
            / denominator
        )
    execution_denominator = max(len(EXECUTION_PROFILES) - 1, 1)
    distances.append(
        abs(_execution_index(left_execution) - _execution_index(right_execution))
        / execution_denominator
    )
    return mean(distances) if distances else 0.0


def _screen_stable_candidates(
    symbol: str,
    market: Market,
    train: list[Candle],
    strategy_name: str,
    config: MetaAllocationConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    quality_config = ProfitQualityConfig(
        train_size=config.train_size,
        test_size=config.test_size,
        max_candidates_per_strategy=config.max_candidates_per_strategy,
        stability_screen_candidates=config.stability_screen_candidates,
        starting_cash=config.starting_cash,
        stability_policy=config.stability_policy,
    )
    gate_config = _gate_score_config(quality_config)
    candidates: list[dict[str, Any]] = []
    for strategy_parameters, execution_parameters in _candidate_grid(strategy_name, quality_config):
        metrics = _run_full_candidate(
            symbol,
            market,
            train,
            strategy_name,
            strategy_parameters,
            execution_parameters,
            config.starting_cash,
        )
        candidates.append(
            {
                "strategy_parameters": strategy_parameters,
                "execution_parameters": execution_parameters,
                "train_metrics": result_metrics(metrics),
                "base_score": _candidate_score(result_metrics(metrics), gate_config),
            }
        )
    candidates.sort(key=lambda item: float(item["base_score"]), reverse=True)
    naive = candidates[0]
    stable: list[dict[str, Any]] = []
    for candidate in candidates[: config.stability_screen_candidates]:
        folds = _fold_metrics(
            symbol,
            market,
            train,
            strategy_name,
            candidate["strategy_parameters"],
            candidate["execution_parameters"],
            quality_config,
        )
        summary = summarize_fold_metrics(folds)
        reasons = stability_reasons(
            candidate["train_metrics"],
            summary,
            config.stability_policy,
        )
        summary = with_stability_flag(summary, reasons)
        enriched = {
            **candidate,
            "fold_metrics": folds,
            "training_stability": summary,
            "selection_reasons": reasons,
            "stability_score": stability_adjusted_score(
                float(candidate["base_score"]),
                summary,
                config.stability_policy,
            ),
        }
        if not reasons:
            stable.append(enriched)
    stable.sort(key=lambda item: float(item["stability_score"]), reverse=True)
    return stable, naive


def _run_full_candidate(
    symbol: str,
    market: Market,
    candles: list[Candle],
    strategy_name: str,
    strategy_parameters: dict[str, Any],
    execution_parameters: dict[str, Any],
    starting_cash: float,
    *,
    trade_start_index: int = 0,
) -> BacktestResult:
    lookback = int(strategy_parameters.get("lookback", 10))
    return PaperTrader(
        market,
        build_strategy(strategy_name, strategy_parameters),
        starting_cash=starting_cash,
        config=_execution_config(execution_parameters, warmup_bars=lookback + 1),
    ).run(symbol, candles, trade_start_index=trade_start_index)


def select_parameter_plateau(
    strategy_name: str,
    stable_candidates: list[dict[str, Any]],
    config: MetaAllocationConfig,
) -> dict[str, Any]:
    if not stable_candidates:
        return {
            "ensemble": [],
            "plateau": [],
            "consensus_strength": 0.0,
            "reasons": ["no_temporally_stable_candidates"],
        }

    plateaus: list[tuple[float, list[dict[str, Any]]]] = []
    for anchor in stable_candidates:
        members = [
            candidate
            for candidate in stable_candidates
            if parameter_distance(
                strategy_name,
                anchor["strategy_parameters"],
                anchor["execution_parameters"],
                candidate["strategy_parameters"],
                candidate["execution_parameters"],
            )
            <= config.plateau_radius
        ]
        if len(members) < config.min_plateau_members:
            continue
        score = mean(float(member["stability_score"]) for member in members)
        plateaus.append((score, members))

    if not plateaus:
        return {
            "ensemble": [],
            "plateau": [],
            "consensus_strength": 0.0,
            "reasons": ["stable_candidates_are_isolated_parameter_peaks"],
        }

    _, plateau = max(plateaus, key=lambda item: (item[0], len(item[1])))
    plateau = sorted(
        plateau,
        key=lambda item: float(item["stability_score"]),
        reverse=True,
    )
    ensemble = plateau[: config.ensemble_candidates]
    fold_agreement = mean(
        float(candidate["training_stability"].get("positive_active_fold_fraction", 0.0))
        for candidate in ensemble
    )
    structural_support = min(1.0, len(ensemble) / config.ensemble_candidates)
    scores = [float(candidate["stability_score"]) for candidate in ensemble]
    if len(scores) <= 1:
        score_agreement = 0.0
    else:
        scale = max(abs(mean(scores)), 1e-9)
        score_agreement = _clamp(1.0 - pstdev(scores) / scale, 0.0, 1.0)
    consensus = _clamp(
        0.45 * fold_agreement + 0.35 * structural_support + 0.20 * score_agreement,
        0.0,
        1.0,
    )
    reasons = (
        []
        if consensus >= config.min_consensus_strength
        else ["parameter_consensus_below_threshold"]
    )
    return {
        "ensemble": ensemble,
        "plateau": plateau,
        "consensus_strength": consensus,
        "reasons": reasons,
    }


def exposure_from_confidence(
    consensus_strength: float,
    annualized_volatility: float,
    config: MetaAllocationConfig,
) -> float:
    if consensus_strength < config.min_consensus_strength:
        return 0.0
    if annualized_volatility <= 1e-12:
        volatility_multiplier = 1.0
    else:
        volatility_multiplier = _clamp(
            config.target_annual_volatility / annualized_volatility,
            0.20,
            1.0,
        )
    exposure = config.max_total_exposure * consensus_strength * volatility_multiplier
    if exposure < config.min_total_exposure:
        return 0.0
    return _clamp(exposure, config.min_total_exposure, config.max_total_exposure)


def _candidate_weights(candidates: list[dict[str, Any]]) -> list[float]:
    if not candidates:
        return []
    scores = [float(candidate["stability_score"]) for candidate in candidates]
    floor = min(scores)
    shifted = [score - floor + 1e-9 for score in scores]
    total = sum(shifted)
    return [value / total for value in shifted]


def _pad_curve(curve: list[float], size: int) -> list[float]:
    if not curve:
        return [0.0] * size
    if len(curve) >= size:
        return curve[:size]
    return [*curve, *([curve[-1]] * (size - len(curve)))]


def _combined_metrics(
    curves: list[tuple[float, list[float]]],
    results: list[tuple[float, BacktestResult]],
    starting_cash: float,
    cash_reserve: float,
    buy_and_hold_return: float,
) -> tuple[dict[str, float | int], list[float]]:
    size = max((len(curve) for _, curve in curves), default=1)
    combined_curve = [cash_reserve] * size
    for _, curve in curves:
        padded = _pad_curve(curve, size)
        combined_curve = [left + right for left, right in zip(combined_curve, padded)]

    trades = [trade for _, result in results for trade in result.trades]
    pnls = [trade.net_pnl for trade in trades]
    ending_cash = combined_curve[-1] if combined_curve else starting_cash
    total_fees = sum(result.total_fees for _, result in results)
    total_tax = sum(result.total_tax for _, result in results)
    total_slippage = sum(result.total_slippage for _, result in results)
    gross_return = sum(
        allocation * result.gross_return / starting_cash
        for allocation, result in results
    )
    net_return = (ending_cash - starting_cash) / starting_cash
    drawdown = max_drawdown(combined_curve)
    returns = period_returns(combined_curve)
    turnover = sum(
        allocation * result.turnover / starting_cash
        for allocation, result in results
    )
    trade_count = len(trades)
    holding = sum(trade.holding_bars for trade in trades)
    cost_weight = sum(allocation for allocation, _ in results)
    weighted_cost_drag = (
        sum(allocation * result.cost_drag_ratio for allocation, result in results)
        / cost_weight
        if cost_weight > 0
        else 0.0
    )
    weighted_exposure = (
        sum(allocation * result.exposure for allocation, result in results)
        / starting_cash
    )
    metrics: dict[str, float | int] = {
        "net_return": net_return,
        "gross_return": gross_return,
        "cash_return": 0.0,
        "buy_and_hold_return": buy_and_hold_return,
        "excess_return": net_return - buy_and_hold_return,
        "max_drawdown": drawdown,
        "win_rate": (
            sum(pnl > 0 for pnl in pnls) / len(pnls)
            if pnls
            else 0.0
        ),
        "trades": trade_count,
        "trades_per_100_bars": trade_count / max(size - 1, 1) * 100.0,
        "average_holding_bars": holding / trade_count if trade_count else 0.0,
        "turnover": turnover,
        "cost_drag_ratio": weighted_cost_drag,
        "total_fees": total_fees,
        "total_tax": total_tax,
        "total_slippage": total_slippage,
        "ending_cash": ending_cash,
        "sharpe_ratio": sharpe_ratio(returns, 365),
        "sortino_ratio": sortino_ratio(returns, 365),
        "profit_factor": profit_factor(pnls),
        "calmar_ratio": net_return / drawdown if drawdown > 0 else 0.0,
        "exposure": weighted_exposure,
    }
    return metrics, combined_curve


def _cash_curve(length: int, starting_cash: float) -> list[float]:
    return [starting_cash] * max(1, length)


def _evaluate_candidate_on_unseen(
    symbol: str,
    market: Market,
    train: list[Candle],
    unseen: list[Candle],
    strategy_name: str,
    candidate: dict[str, Any],
    starting_cash: float,
) -> BacktestResult:
    strategy_parameters = candidate["strategy_parameters"]
    execution_parameters = candidate["execution_parameters"]
    lookback = int(strategy_parameters.get("lookback", 10))
    warmup_count = min(len(train), max(30, lookback + 1))
    evaluation = [*train[-warmup_count:], *unseen]
    return _run_full_candidate(
        symbol,
        market,
        evaluation,
        strategy_name,
        strategy_parameters,
        execution_parameters,
        starting_cash,
        trade_start_index=warmup_count,
    )


def _evaluate_asset_period(
    symbol: str,
    market: Market,
    strategy_name: str,
    period_index: int,
    train: list[Candle],
    unseen: list[Candle],
    config: MetaAllocationConfig,
) -> _PeriodEvidence:
    stable, naive = _screen_stable_candidates(
        symbol,
        market,
        train,
        strategy_name,
        config,
    )
    plateau = select_parameter_plateau(strategy_name, stable, config)
    volatility = _annualized_volatility(train)
    total_exposure = exposure_from_confidence(
        float(plateau["consensus_strength"]),
        volatility,
        config,
    )

    quality_config = ProfitQualityConfig(
        train_size=config.train_size,
        test_size=config.test_size,
        max_candidates_per_strategy=config.max_candidates_per_strategy,
        stability_screen_candidates=config.stability_screen_candidates,
        starting_cash=config.starting_cash,
        stability_policy=config.stability_policy,
    )
    v05_candidate = stable[0] if stable else None
    naive_result = _evaluate_candidate_on_unseen(
        symbol,
        market,
        train,
        unseen,
        strategy_name,
        naive,
        config.starting_cash,
    )
    naive_metrics = result_metrics(naive_result)
    naive_curve = naive_result.equity_curve
    if v05_candidate is None:
        v05_metrics = _cash_metrics(unseen, config.starting_cash)
        v05_curve = _cash_curve(len(naive_curve), config.starting_cash)
    else:
        v05_result = _evaluate_candidate_on_unseen(
            symbol,
            market,
            train,
            unseen,
            strategy_name,
            v05_candidate,
            config.starting_cash,
        )
        v05_metrics = result_metrics(v05_result)
        v05_curve = v05_result.equity_curve

    reasons = list(plateau["reasons"])
    if total_exposure <= 0 and not reasons:
        reasons.append("confidence_and_volatility_sizing_below_minimum")
    if reasons:
        meta_metrics = _cash_metrics(unseen, config.starting_cash)
        meta_curve = _cash_curve(
            max(len(naive_curve), len(v05_curve)),
            config.starting_cash,
        )
        allocations: list[dict[str, Any]] = []
    else:
        ensemble = list(plateau["ensemble"])
        weights = _candidate_weights(ensemble)
        allocations = []
        curves: list[tuple[float, list[float]]] = []
        results: list[tuple[float, BacktestResult]] = []
        for candidate, weight in zip(ensemble, weights):
            allocation = config.starting_cash * total_exposure * weight
            result = _evaluate_candidate_on_unseen(
                symbol,
                market,
                train,
                unseen,
                strategy_name,
                candidate,
                allocation,
            )
            curves.append((allocation, result.equity_curve))
            results.append((allocation, result))
            allocations.append(
                {
                    "strategy_parameters": candidate["strategy_parameters"],
                    "execution_parameters": candidate["execution_parameters"],
                    "stability_score": candidate["stability_score"],
                    "allocation_fraction": allocation / config.starting_cash,
                }
            )
        first_open = unseen[0].open if unseen else 0.0
        buy_and_hold = (
            (unseen[-1].close - first_open) / first_open
            if unseen and first_open > 0
            else 0.0
        )
        reserve = config.starting_cash * (1.0 - total_exposure)
        meta_metrics, meta_curve = _combined_metrics(
            curves,
            results,
            config.starting_cash,
            reserve,
            buy_and_hold,
        )

    public = MetaAllocationPeriod(
        symbol=symbol,
        strategy=strategy_name,
        period=period_index,
        train_start=train[0].timestamp.isoformat(),
        train_end=train[-1].timestamp.isoformat(),
        unseen_start=unseen[0].timestamp.isoformat(),
        unseen_end=unseen[-1].timestamp.isoformat(),
        abstained=bool(reasons),
        abstention_reasons=reasons,
        plateau_size=len(plateau["plateau"]),
        ensemble_size=len(plateau["ensemble"]),
        consensus_strength=float(plateau["consensus_strength"]),
        annualized_training_volatility=volatility,
        total_exposure=0.0 if reasons else total_exposure,
        candidate_allocations=allocations,
        meta_metrics=meta_metrics,
        v05_metrics=v05_metrics,
        naive_metrics=naive_metrics,
        improvement_vs_v05=float(meta_metrics["net_return"]) - float(v05_metrics["net_return"]),
        improvement_vs_naive=float(meta_metrics["net_return"]) - float(naive_metrics["net_return"]),
    )
    return _PeriodEvidence(
        public=public,
        train=train,
        meta_curve=meta_curve,
        v05_curve=v05_curve,
        naive_curve=naive_curve,
    )


def correlation_filter(
    candidates: list[tuple[str, float, list[float]]],
    max_pair_correlation: float,
) -> tuple[list[str], list[str]]:
    selected: list[tuple[str, float, list[float]]] = []
    rejected: list[str] = []
    for item in sorted(candidates, key=lambda value: value[1], reverse=True):
        symbol, _, returns = item
        if any(
            _pearson(returns, other_returns) > max_pair_correlation
            for _, _, other_returns in selected
        ):
            rejected.append(symbol)
        else:
            selected.append(item)
    return [symbol for symbol, _, _ in selected], rejected


def next_drawdown_multiplier(
    previous_multiplier: float,
    previous_drawdown: float,
    config: MetaAllocationConfig,
) -> float:
    if previous_drawdown >= config.drawdown_brake_trigger:
        return config.drawdown_brake_multiplier
    return min(1.0, previous_multiplier + config.drawdown_recovery_step)


def _portfolio_metrics_from_curves(
    evidence: list[_PeriodEvidence],
    weights: dict[str, float],
    curve_name: str,
    starting_cash: float,
) -> tuple[dict[str, float | int], list[float]]:
    curves = [
        getattr(item, curve_name)
        for item in evidence
        if item.public.symbol in weights
    ]
    size = max((len(curve) for curve in curves), default=1)
    cash_weight = max(0.0, 1.0 - sum(weights.values()))
    combined = [starting_cash * cash_weight] * size
    for item in evidence:
        weight = weights.get(item.public.symbol, 0.0)
        if weight <= 0:
            continue
        curve = _pad_curve(getattr(item, curve_name), size)
        combined = [
            total + weight * value
            for total, value in zip(combined, curve)
        ]
    ending = combined[-1]
    net_return = (ending - starting_cash) / starting_cash
    drawdown = max_drawdown(combined)
    returns = period_returns(combined)
    metric_field = {
        "meta_curve": "meta_metrics",
        "v05_curve": "v05_metrics",
        "naive_curve": "naive_metrics",
    }[curve_name]
    metrics: dict[str, float | int] = {
        "net_return": net_return,
        "gross_return": net_return,
        "cash_return": 0.0,
        "buy_and_hold_return": 0.0,
        "excess_return": net_return,
        "max_drawdown": drawdown,
        "trades": sum(
            int(getattr(item.public, metric_field)["trades"])
            for item in evidence
            if item.public.symbol in weights
        ),
        "ending_cash": ending,
        "sharpe_ratio": sharpe_ratio(returns, 365),
        "sortino_ratio": sortino_ratio(returns, 365),
        "calmar_ratio": net_return / drawdown if drawdown > 0 else 0.0,
        "exposure": sum(weights.values()),
    }
    return metrics, combined


def _portfolio_periods(
    strategy_name: str,
    evidence: list[_PeriodEvidence],
    config: MetaAllocationConfig,
) -> list[PortfolioPeriod]:
    grouped: dict[int, list[_PeriodEvidence]] = {}
    for item in evidence:
        grouped.setdefault(item.public.period, []).append(item)

    periods: list[PortfolioPeriod] = []
    risk_multiplier = 1.0
    previous_drawdown = 0.0
    for period_index in sorted(grouped):
        current = grouped[period_index]
        if periods:
            risk_multiplier = next_drawdown_multiplier(
                risk_multiplier,
                previous_drawdown,
                config,
            )
        correlation_candidates = [
            (
                item.public.symbol,
                item.public.consensus_strength,
                _close_returns(item.train),
            )
            for item in current
            if not item.public.abstained
        ]
        selected, rejected = correlation_filter(
            correlation_candidates,
            config.max_pair_correlation,
        )
        active = [item for item in current if item.public.symbol in selected]
        raw_scores = {
            item.public.symbol: (
                item.public.consensus_strength
                / max(item.public.annualized_training_volatility, 1e-6)
            )
            for item in active
        }
        raw_total = sum(raw_scores.values())
        max_total = min(
            config.max_total_exposure,
            1.0 - config.min_cash_reserve,
        ) * risk_multiplier
        weights: dict[str, float] = {}
        if raw_total > 0:
            for symbol, score in raw_scores.items():
                weights[symbol] = min(
                    config.max_asset_exposure,
                    max_total * score / raw_total,
                )
            allocated = sum(weights.values())
            if allocated > max_total and allocated > 0:
                scale = max_total / allocated
                weights = {symbol: weight * scale for symbol, weight in weights.items()}

        meta_metrics, _ = _portfolio_metrics_from_curves(
            current,
            weights,
            "meta_curve",
            config.starting_cash,
        )
        v05_metrics, _ = _portfolio_metrics_from_curves(
            current,
            weights,
            "v05_curve",
            config.starting_cash,
        )
        naive_metrics, _ = _portfolio_metrics_from_curves(
            current,
            weights,
            "naive_curve",
            config.starting_cash,
        )
        previous_drawdown = float(meta_metrics["max_drawdown"])
        first = min(current, key=lambda item: item.public.unseen_start).public
        periods.append(
            PortfolioPeriod(
                strategy=strategy_name,
                period=period_index,
                unseen_start=first.unseen_start,
                unseen_end=first.unseen_end,
                selected_symbols=sorted(weights),
                correlation_rejections=sorted(rejected),
                asset_weights=weights,
                cash_weight=max(0.0, 1.0 - sum(weights.values())),
                risk_multiplier=risk_multiplier,
                meta_metrics=meta_metrics,
                v05_metrics=v05_metrics,
                naive_metrics=naive_metrics,
                improvement_vs_v05=float(meta_metrics["net_return"])
                - float(v05_metrics["net_return"]),
                improvement_vs_naive=float(meta_metrics["net_return"])
                - float(naive_metrics["net_return"]),
            )
        )
    return periods


def _summarize_strategy(
    strategy_name: str,
    evidence: list[_PeriodEvidence],
    portfolio_periods: list[PortfolioPeriod],
    config: MetaAllocationConfig,
) -> MetaStrategyResult:
    periods = [item.public for item in evidence]
    deployed = [item for item in periods if not item.abstained]
    meta_returns = [float(item.meta_metrics["net_return"]) for item in periods]
    v05_returns = [float(item.v05_metrics["net_return"]) for item in periods]
    naive_returns = [float(item.naive_metrics["net_return"]) for item in periods]
    improvements_v05 = [item.improvement_vs_v05 for item in periods]
    improvements_naive = [item.improvement_vs_naive for item in periods]
    positive_fraction = (
        sum(float(item.meta_metrics["net_return"]) > 0 for item in deployed)
        / len(deployed)
        if deployed
        else 0.0
    )
    beats_v05 = (
        sum(item.improvement_vs_v05 > 0 for item in periods) / len(periods)
        if periods
        else 0.0
    )
    portfolio_returns = [
        float(item.meta_metrics["net_return"])
        for item in portfolio_periods
    ]
    portfolio_v05_returns = [
        float(item.v05_metrics["net_return"])
        for item in portfolio_periods
    ]
    portfolio_improvements = [
        item.improvement_vs_v05
        for item in portfolio_periods
    ]
    portfolio_positive = (
        sum(value > 0 for value in portfolio_returns) / len(portfolio_returns)
        if portfolio_returns
        else 0.0
    )

    average_meta = mean(meta_returns) if meta_returns else 0.0
    average_v05 = mean(v05_returns) if v05_returns else 0.0
    average_naive = mean(naive_returns) if naive_returns else 0.0
    average_improvement_v05 = mean(improvements_v05) if improvements_v05 else 0.0
    average_improvement_naive = mean(improvements_naive) if improvements_naive else 0.0
    worst_drawdown = max(
        (float(item.meta_metrics["max_drawdown"]) for item in periods),
        default=0.0,
    )
    portfolio_average = mean(portfolio_returns) if portfolio_returns else 0.0
    portfolio_average_v05 = mean(portfolio_v05_returns) if portfolio_v05_returns else 0.0
    portfolio_average_improvement = (
        mean(portfolio_improvements)
        if portfolio_improvements
        else 0.0
    )
    portfolio_worst_drawdown = max(
        (float(item.meta_metrics["max_drawdown"]) for item in portfolio_periods),
        default=0.0,
    )

    reasons: list[str] = []
    if len(deployed) < config.min_deployed_periods:
        reasons.append("too_few_meta_deployed_periods")
    if average_meta <= 0:
        reasons.append("average_meta_return_not_positive")
    if average_improvement_v05 <= config.min_average_improvement_vs_v05:
        reasons.append("no_average_improvement_over_v05")
    if positive_fraction < config.min_positive_deployed_fraction:
        reasons.append("too_few_positive_meta_periods")
    if beats_v05 < config.min_meta_beats_v05_fraction:
        reasons.append("meta_did_not_beat_v05_often_enough")
    if worst_drawdown > config.max_unseen_drawdown:
        reasons.append("asset_level_drawdown_too_high")
    if portfolio_average <= 0:
        reasons.append("portfolio_average_return_not_positive")
    if portfolio_average_improvement <= config.min_average_improvement_vs_v05:
        reasons.append("portfolio_did_not_improve_over_v05")
    if portfolio_positive < 0.50:
        reasons.append("portfolio_positive_period_fraction_too_low")
    if portfolio_worst_drawdown > config.max_unseen_drawdown:
        reasons.append("portfolio_drawdown_too_high")

    return MetaStrategyResult(
        strategy=strategy_name,
        approved=not reasons,
        reasons=reasons or ["Meta-allocation improved unseen profit quality."],
        periods=periods,
        portfolio_periods=portfolio_periods,
        deployed_periods=len(deployed),
        abstained_periods=len(periods) - len(deployed),
        average_meta_return=average_meta,
        average_v05_return=average_v05,
        average_naive_return=average_naive,
        average_improvement_vs_v05=average_improvement_v05,
        average_improvement_vs_naive=average_improvement_naive,
        positive_deployed_fraction=positive_fraction,
        meta_beats_v05_fraction=beats_v05,
        worst_meta_drawdown=worst_drawdown,
        portfolio_average_return=portfolio_average,
        portfolio_average_v05_return=portfolio_average_v05,
        portfolio_average_improvement_vs_v05=portfolio_average_improvement,
        portfolio_positive_fraction=portfolio_positive,
        portfolio_worst_drawdown=portfolio_worst_drawdown,
    )


def evaluate_meta_allocation(
    folder: str | Path,
    market: Market = Market.CRYPTO,
    config: MetaAllocationConfig | None = None,
) -> MetaAllocationReport:
    config = config or MetaAllocationConfig()
    histories = load_histories(folder)
    results: list[MetaStrategyResult] = []
    for strategy_name in STRATEGIES:
        evidence: list[_PeriodEvidence] = []
        for symbol, candles in histories.items():
            windows = independent_train_test_windows(
                candles,
                config.train_size,
                config.test_size,
            )
            for period_index, (train, unseen) in enumerate(windows, start=1):
                evidence.append(
                    _evaluate_asset_period(
                        symbol,
                        market,
                        strategy_name,
                        period_index,
                        train,
                        unseen,
                        config,
                    )
                )
        portfolio = _portfolio_periods(strategy_name, evidence, config)
        results.append(
            _summarize_strategy(
                strategy_name,
                evidence,
                portfolio,
                config,
            )
        )

    approved = [item for item in results if item.approved]
    champion = (
        max(
            approved,
            key=lambda item: (
                item.portfolio_average_return,
                item.average_improvement_vs_v05,
                -item.portfolio_worst_drawdown,
            ),
        ).strategy
        if approved
        else None
    )
    accepted = bool(approved)
    reasons = (
        [
            f"Meta-allocation gate passed for: {', '.join(item.strategy for item in approved)}.",
            f"Champion for another forward paper comparison: {champion}.",
        ]
        if accepted
        else [
            "No strategy proved positive unseen returns after plateau consensus and portfolio controls.",
            "Keep all real-money and continuous-forward authorization closed.",
        ]
    )
    return MetaAllocationReport(
        schema_version=META_ALLOCATION_SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        market=market.value,
        dataset_fingerprint=dataset_fingerprint(histories),
        symbols=sorted(histories),
        config=asdict(config),
        strategies=results,
        accepted=accepted,
        champion_strategy=champion,
        reasons=reasons,
    )


def write_meta_allocation_report(
    path: str | Path,
    report: MetaAllocationReport,
) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(asdict(report), indent=2),
        encoding="utf-8",
    )
    temporary.replace(output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate parameter plateaus, consensus sizing and portfolio risk "
            "controls against frozen v0.5 and naive v0.4-style baselines."
        )
    )
    parser.add_argument("--folder", required=True)
    parser.add_argument(
        "--market",
        choices=[item.value for item in Market],
        default=Market.CRYPTO.value,
    )
    parser.add_argument("--train-size", type=int, default=180)
    parser.add_argument("--test-size", type=int, default=60)
    parser.add_argument("--max-candidates", type=int, default=27)
    parser.add_argument("--screen-candidates", type=int, default=9)
    parser.add_argument("--json-out", default="reports/meta_allocation_v06.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = MetaAllocationConfig(
        train_size=args.train_size,
        test_size=args.test_size,
        max_candidates_per_strategy=args.max_candidates,
        stability_screen_candidates=args.screen_candidates,
    )
    report = evaluate_meta_allocation(
        args.folder,
        market=Market(args.market),
        config=config,
    )
    write_meta_allocation_report(args.json_out, report)
    print(json.dumps(asdict(report), indent=2))
    return 0 if report.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
