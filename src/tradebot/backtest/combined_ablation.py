from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from tradebot.backtest.alpha_discovery import (
    ALPHA_PARAMETER_GRIDS,
    REQUIRED_SYMBOLS,
    V05_STRATEGIES,
    AlphaDiscoveryConfig,
    _candidate_grid as alpha_candidate_grid,
    _run_alpha_candidate,
    _slice_histories,
    _strategy_warmup,
    _training_fold_metrics,
    build_alpha_strategy,
    load_fixed_histories,
)
from tradebot.backtest.meta_allocation import (
    MetaAllocationConfig,
    _annualized_volatility,
    _candidate_weights,
    _close_returns,
    _combined_metrics,
    _pad_curve,
    correlation_filter,
    evaluate_meta_allocation,
    exposure_from_confidence,
    next_drawdown_multiplier,
)
from tradebot.backtest.metrics import max_drawdown, period_returns, sharpe_ratio, sortino_ratio
from tradebot.backtest.paper_trader import PaperTrader
from tradebot.backtest.profit_quality_gate import (
    ProfitQualityConfig,
    _cash_metrics,
    _select_candidates as select_v05_candidates,
)
from tradebot.backtest.research_gate import (
    EXECUTION_PROFILES,
    ResearchGateConfig,
    _candidate_score,
    _execution_config,
    dataset_fingerprint,
    independent_train_test_windows,
)
from tradebot.backtest.selection_stability import (
    TemporalStabilityPolicy,
    stability_adjusted_score,
    stability_reasons,
    summarize_fold_metrics,
    with_stability_flag,
)
from tradebot.backtest.walk_forward import build_strategy, result_metrics
from tradebot.models import BacktestResult, Candle, Market


COMBINED_SCHEMA_VERSION = "1.0"
COMBINED_FAMILY = "cross_asset_relative_strength"


@dataclass(frozen=True)
class CombinedAblationConfig:
    train_size: int = 180
    test_size: int = 60
    max_candidates: int = 72
    screen_candidates: int = 24
    ensemble_candidates: int = 3
    min_plateau_members: int = 2
    plateau_radius: float = 0.34
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
    min_profitable_assets: int = 2
    max_drawdown: float = 0.20
    starting_cash: float = 100000.0
    stability_policy: TemporalStabilityPolicy = field(default_factory=TemporalStabilityPolicy)

    def __post_init__(self) -> None:
        if self.train_size != 180 or self.test_size != 60:
            raise ValueError("Combined ablation requires fixed 180/60 windows")
        if not 1 <= self.screen_candidates <= self.max_candidates:
            raise ValueError("screen_candidates must be within the candidate budget")
        if not 1 <= self.ensemble_candidates <= self.screen_candidates:
            raise ValueError("ensemble_candidates must be within the screening budget")
        if self.min_plateau_members < 2:
            raise ValueError("min_plateau_members must be at least two")
        if not 0 < self.plateau_radius <= 1:
            raise ValueError("plateau_radius must be between zero and one")
        if not 0 <= self.min_consensus_strength <= 1:
            raise ValueError("min_consensus_strength must be between zero and one")
        if not 0 <= self.min_total_exposure <= self.max_total_exposure <= 1:
            raise ValueError("exposure bounds must be between zero and one")
        if self.max_total_exposure > 1.0 - self.min_cash_reserve + 1e-12:
            raise ValueError("max_total_exposure conflicts with min_cash_reserve")
        if not 0 < self.max_asset_exposure <= 1:
            raise ValueError("max_asset_exposure must be between zero and one")
        if not -1 <= self.max_pair_correlation <= 1:
            raise ValueError("max_pair_correlation must be between -1 and one")
        if self.min_deployed_periods < 1:
            raise ValueError("min_deployed_periods must be positive")
        if not 0 <= self.min_positive_deployed_fraction <= 1:
            raise ValueError("min_positive_deployed_fraction must be between zero and one")
        if self.min_profitable_assets < 2:
            raise ValueError("at least two profitable assets are required")
        if not 0 < self.max_drawdown < 1:
            raise ValueError("max_drawdown must be between zero and one")
        if self.starting_cash <= 0:
            raise ValueError("starting_cash must be positive")


@dataclass
class CombinedAssetPeriod:
    symbol: str
    period: int
    train_start: str
    train_end: str
    unseen_start: str
    unseen_end: str
    track_a_deployed: bool
    combined_deployed: bool
    combined_abstention_reasons: list[str]
    stable_candidate_count: int
    plateau_size: int
    ensemble_size: int
    consensus_strength: float
    annualized_training_volatility: float
    total_exposure: float
    candidate_allocations: list[dict[str, Any]]
    v05_reference_strategy: str
    v05_metrics: dict[str, float | int]
    track_a_metrics: dict[str, float | int]
    combined_metrics: dict[str, float | int]
    combined_improvement_vs_v05: float
    combined_improvement_vs_track_a: float


@dataclass
class CombinedPortfolioPeriod:
    period: int
    unseen_start: str
    unseen_end: str
    selected_symbols: list[str]
    correlation_rejections: list[str]
    asset_weights: dict[str, float]
    cash_weight: float
    risk_multiplier: float
    v05_metrics: dict[str, float | int]
    track_a_metrics: dict[str, float | int]
    combined_metrics: dict[str, float | int]
    combined_improvement_vs_v05: float
    combined_improvement_vs_track_a: float


@dataclass
class CombinedAblationReport:
    schema_version: str
    generated_at: str
    market: str
    dataset_fingerprint: str
    symbols: list[str]
    family: str
    config: dict[str, Any]
    periods: list[CombinedAssetPeriod]
    portfolio_periods: list[CombinedPortfolioPeriod]
    track_a_average_return: float
    combined_average_return: float
    v05_average_return: float
    part_b_best_strategy: str | None
    part_b_best_portfolio_average_return: float
    combined_average_improvement_vs_track_a: float
    combined_average_improvement_vs_v05: float
    combined_deployed_periods: int
    combined_positive_deployed_fraction: float
    combined_worst_drawdown: float
    combined_asset_average_returns: dict[str, float]
    combined_profitable_assets: list[str]
    track_a_portfolio_average_return: float
    combined_portfolio_average_return: float
    v05_portfolio_average_return: float
    combined_portfolio_improvement_vs_track_a: float
    combined_portfolio_improvement_vs_v05: float
    combined_portfolio_worst_drawdown: float
    accepted: bool
    reasons: list[str]
    paper_only: bool = True
    authorizes_real_trading: bool = False


@dataclass
class _AssetEvidence:
    public: CombinedAssetPeriod
    train: list[Candle]
    v05_curve: list[float]
    track_a_curve: list[float]
    combined_curve: list[float]


def _allocation_config(config: CombinedAblationConfig) -> MetaAllocationConfig:
    return MetaAllocationConfig(
        train_size=config.train_size,
        test_size=config.test_size,
        max_candidates_per_strategy=27,
        stability_screen_candidates=9,
        ensemble_candidates=config.ensemble_candidates,
        min_plateau_members=config.min_plateau_members,
        plateau_radius=config.plateau_radius,
        min_consensus_strength=config.min_consensus_strength,
        target_annual_volatility=config.target_annual_volatility,
        min_total_exposure=config.min_total_exposure,
        max_total_exposure=config.max_total_exposure,
        min_cash_reserve=config.min_cash_reserve,
        max_asset_exposure=config.max_asset_exposure,
        max_pair_correlation=config.max_pair_correlation,
        drawdown_brake_trigger=config.drawdown_brake_trigger,
        drawdown_brake_multiplier=config.drawdown_brake_multiplier,
        drawdown_recovery_step=config.drawdown_recovery_step,
        min_deployed_periods=config.min_deployed_periods,
        min_positive_deployed_fraction=config.min_positive_deployed_fraction,
        max_unseen_drawdown=config.max_drawdown,
        starting_cash=config.starting_cash,
        stability_policy=config.stability_policy,
    )


def _alpha_config(config: CombinedAblationConfig) -> AlphaDiscoveryConfig:
    return AlphaDiscoveryConfig(
        train_size=config.train_size,
        test_size=config.test_size,
        max_candidates_per_family=config.max_candidates,
        stability_screen_candidates=config.screen_candidates,
        min_deployed_periods=config.min_deployed_periods,
        min_positive_deployed_fraction=config.min_positive_deployed_fraction,
        min_profitable_assets=config.min_profitable_assets,
        max_drawdown=config.max_drawdown,
        starting_cash=config.starting_cash,
        stability_policy=config.stability_policy,
    )


def _execution_index(execution: dict[str, Any]) -> int:
    for index, profile in enumerate(EXECUTION_PROFILES):
        if dict(profile) == dict(execution):
            return index
    return 0


def alpha_parameter_distance(
    left_parameters: dict[str, Any],
    left_execution: dict[str, Any],
    right_parameters: dict[str, Any],
    right_execution: dict[str, Any],
) -> float:
    distances: list[float] = []
    for key, values in ALPHA_PARAMETER_GRIDS[COMBINED_FAMILY].items():
        denominator = max(len(values) - 1, 1)
        try:
            left_index = list(values).index(left_parameters.get(key))
            right_index = list(values).index(right_parameters.get(key))
        except ValueError:
            left_index = right_index = 0
        distances.append(abs(left_index - right_index) / denominator)
    execution_denominator = max(len(EXECUTION_PROFILES) - 1, 1)
    distances.append(
        abs(_execution_index(left_execution) - _execution_index(right_execution))
        / execution_denominator
    )
    return mean(distances) if distances else 0.0


def select_alpha_plateau(
    stable_candidates: list[dict[str, Any]],
    config: CombinedAblationConfig,
) -> dict[str, Any]:
    if not stable_candidates:
        return {
            "plateau": [],
            "ensemble": [],
            "consensus_strength": 0.0,
            "reasons": ["no_temporally_stable_relative_strength_candidates"],
        }
    plateaus: list[tuple[float, list[dict[str, Any]]]] = []
    for anchor in stable_candidates:
        members = [
            candidate
            for candidate in stable_candidates
            if alpha_parameter_distance(
                anchor["strategy_parameters"],
                anchor["execution_parameters"],
                candidate["strategy_parameters"],
                candidate["execution_parameters"],
            )
            <= config.plateau_radius
        ]
        if len(members) >= config.min_plateau_members:
            plateaus.append(
                (
                    mean(float(item["stability_score"]) for item in members),
                    members,
                )
            )
    if not plateaus:
        return {
            "plateau": [],
            "ensemble": [],
            "consensus_strength": 0.0,
            "reasons": ["relative_strength_candidates_are_isolated_parameter_peaks"],
        }
    _, plateau = max(plateaus, key=lambda item: (item[0], len(item[1])))
    plateau = sorted(
        plateau,
        key=lambda item: float(item["stability_score"]),
        reverse=True,
    )
    ensemble = plateau[: config.ensemble_candidates]
    fold_agreement = mean(
        float(item["training_stability"].get("positive_active_fold_fraction", 0.0))
        for item in ensemble
    )
    structural_support = min(1.0, len(ensemble) / config.ensemble_candidates)
    scores = [float(item["stability_score"]) for item in ensemble]
    if len(scores) <= 1:
        score_agreement = 0.0
    else:
        scale = max(abs(mean(scores)), 1e-9)
        score_agreement = min(1.0, max(0.0, 1.0 - pstdev(scores) / scale))
    consensus = min(
        1.0,
        max(
            0.0,
            0.45 * fold_agreement
            + 0.35 * structural_support
            + 0.20 * score_agreement,
        ),
    )
    return {
        "plateau": plateau,
        "ensemble": ensemble,
        "consensus_strength": consensus,
        "reasons": (
            []
            if consensus >= config.min_consensus_strength
            else ["relative_strength_consensus_below_threshold"]
        ),
    }


def _run_alpha_result(
    symbol: str,
    market: Market,
    candles: list[Candle],
    candidate: dict[str, Any],
    peer_histories: dict[str, list[Candle]],
    starting_cash: float,
    *,
    trade_start_index: int = 0,
) -> BacktestResult:
    parameters = candidate["strategy_parameters"]
    execution = candidate["execution_parameters"]
    warmup = _strategy_warmup(COMBINED_FAMILY, parameters)
    strategy = build_alpha_strategy(
        COMBINED_FAMILY,
        parameters,
        symbol=symbol,
        peer_histories=peer_histories,
    )
    return PaperTrader(
        market,
        strategy,
        starting_cash=starting_cash,
        config=_execution_config(execution, warmup_bars=warmup),
    ).run(symbol, candles, trade_start_index=trade_start_index)


def _evaluate_alpha_result(
    symbol: str,
    market: Market,
    train: list[Candle],
    unseen: list[Candle],
    candidate: dict[str, Any],
    histories: dict[str, list[Candle]],
    starting_cash: float,
) -> BacktestResult:
    warmup_count = min(
        len(train),
        max(30, _strategy_warmup(COMBINED_FAMILY, candidate["strategy_parameters"])),
    )
    evaluation = [*train[-warmup_count:], *unseen]
    peer_histories = _slice_histories(
        histories,
        evaluation[0].timestamp,
        evaluation[-1].timestamp,
    )
    return _run_alpha_result(
        symbol,
        market,
        evaluation,
        candidate,
        peer_histories,
        starting_cash,
        trade_start_index=warmup_count,
    )


def _screen_alpha_candidates(
    symbol: str,
    market: Market,
    train: list[Candle],
    histories: dict[str, list[Candle]],
    config: CombinedAblationConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    alpha_config = _alpha_config(config)
    peer_train = _slice_histories(histories, train[0].timestamp, train[-1].timestamp)
    score_config = ResearchGateConfig(max_candidates_per_strategy=config.max_candidates)
    candidates: list[dict[str, Any]] = []
    for parameters, execution in alpha_candidate_grid(COMBINED_FAMILY, alpha_config):
        metrics = _run_alpha_candidate(
            symbol,
            market,
            train,
            COMBINED_FAMILY,
            parameters,
            execution,
            peer_train,
            config.starting_cash,
        )
        candidates.append(
            {
                "strategy_parameters": parameters,
                "execution_parameters": execution,
                "train_metrics": metrics,
                "base_score": _candidate_score(metrics, score_config),
            }
        )
    candidates.sort(key=lambda item: float(item["base_score"]), reverse=True)
    stable: list[dict[str, Any]] = []
    for candidate in candidates[: config.screen_candidates]:
        folds = _training_fold_metrics(
            symbol,
            market,
            train,
            COMBINED_FAMILY,
            candidate["strategy_parameters"],
            candidate["execution_parameters"],
            histories,
            alpha_config,
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
    return stable, candidates[0]


def _cash_curve(length: int, starting_cash: float) -> list[float]:
    return [starting_cash] * max(1, length)


def _run_v05_result(
    symbol: str,
    market: Market,
    train: list[Candle],
    unseen: list[Candle],
    strategy_name: str,
    candidate: dict[str, Any],
    starting_cash: float,
) -> BacktestResult:
    parameters = candidate["strategy_parameters"]
    execution = candidate["execution_parameters"]
    lookback = int(parameters.get("lookback", 10))
    warmup_count = min(len(train), max(30, lookback + 1))
    evaluation = [*train[-warmup_count:], *unseen]
    return PaperTrader(
        market,
        build_strategy(strategy_name, parameters),
        starting_cash=starting_cash,
        config=_execution_config(execution, warmup_bars=lookback + 1),
    ).run(symbol, evaluation, trade_start_index=warmup_count)


def _evaluate_v05_reference(
    symbol: str,
    market: Market,
    train: list[Candle],
    unseen: list[Candle],
    config: CombinedAblationConfig,
) -> tuple[str, dict[str, float | int], list[float]]:
    quality_config = ProfitQualityConfig(
        train_size=config.train_size,
        test_size=config.test_size,
        max_candidates_per_strategy=120,
        stability_screen_candidates=24,
        starting_cash=config.starting_cash,
        stability_policy=config.stability_policy,
    )
    choices: list[tuple[str, float, dict[str, Any]]] = []
    for strategy_name in V05_STRATEGIES:
        selection = select_v05_candidates(
            symbol,
            market,
            train,
            strategy_name,
            quality_config,
        )
        stable = selection["stable"]
        if stable is not None:
            choices.append((strategy_name, float(stable["stability_score"]), stable))
    if not choices:
        length = max(2, len(unseen) + 1)
        return (
            "cash",
            _cash_metrics(unseen, config.starting_cash),
            _cash_curve(length, config.starting_cash),
        )
    strategy_name, _, candidate = max(choices, key=lambda item: (item[1], item[0]))
    result = _run_v05_result(
        symbol,
        market,
        train,
        unseen,
        strategy_name,
        candidate,
        config.starting_cash,
    )
    return strategy_name, result_metrics(result), result.equity_curve


def _evaluate_asset_period(
    symbol: str,
    market: Market,
    period_index: int,
    train: list[Candle],
    unseen: list[Candle],
    histories: dict[str, list[Candle]],
    config: CombinedAblationConfig,
) -> _AssetEvidence:
    stable, _ = _screen_alpha_candidates(
        symbol,
        market,
        train,
        histories,
        config,
    )
    track_a_candidate = stable[0] if stable else None
    plateau = select_alpha_plateau(stable, config)
    volatility = _annualized_volatility(train)
    allocation_config = _allocation_config(config)
    exposure = exposure_from_confidence(
        float(plateau["consensus_strength"]),
        volatility,
        allocation_config,
    )
    reasons = list(plateau["reasons"])
    if exposure <= 0 and not reasons:
        reasons.append("combined_exposure_below_minimum")

    reference_name, v05_metrics, v05_curve = _evaluate_v05_reference(
        symbol,
        market,
        train,
        unseen,
        config,
    )
    reference_length = len(v05_curve)
    if track_a_candidate is None:
        track_a_metrics = _cash_metrics(unseen, config.starting_cash)
        track_a_curve = _cash_curve(reference_length, config.starting_cash)
    else:
        track_a_result = _evaluate_alpha_result(
            symbol,
            market,
            train,
            unseen,
            track_a_candidate,
            histories,
            config.starting_cash,
        )
        track_a_metrics = result_metrics(track_a_result)
        track_a_curve = track_a_result.equity_curve
        reference_length = max(reference_length, len(track_a_curve))

    allocations: list[dict[str, Any]] = []
    if reasons:
        combined_metrics = _cash_metrics(unseen, config.starting_cash)
        combined_curve = _cash_curve(reference_length, config.starting_cash)
        exposure = 0.0
    else:
        candidates = list(plateau["ensemble"])
        weights = _candidate_weights(candidates)
        curves: list[tuple[float, list[float]]] = []
        results: list[tuple[float, BacktestResult]] = []
        for candidate, weight in zip(candidates, weights):
            allocation = config.starting_cash * exposure * weight
            result = _evaluate_alpha_result(
                symbol,
                market,
                train,
                unseen,
                candidate,
                histories,
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
        combined_metrics, combined_curve = _combined_metrics(
            curves,
            results,
            config.starting_cash,
            config.starting_cash * (1.0 - exposure),
            buy_and_hold,
        )

    public = CombinedAssetPeriod(
        symbol=symbol,
        period=period_index,
        train_start=train[0].timestamp.isoformat(),
        train_end=train[-1].timestamp.isoformat(),
        unseen_start=unseen[0].timestamp.isoformat(),
        unseen_end=unseen[-1].timestamp.isoformat(),
        track_a_deployed=track_a_candidate is not None,
        combined_deployed=not reasons,
        combined_abstention_reasons=reasons,
        stable_candidate_count=len(stable),
        plateau_size=len(plateau["plateau"]),
        ensemble_size=len(plateau["ensemble"]),
        consensus_strength=float(plateau["consensus_strength"]),
        annualized_training_volatility=volatility,
        total_exposure=exposure,
        candidate_allocations=allocations,
        v05_reference_strategy=reference_name,
        v05_metrics=v05_metrics,
        track_a_metrics=track_a_metrics,
        combined_metrics=combined_metrics,
        combined_improvement_vs_v05=(
            float(combined_metrics["net_return"]) - float(v05_metrics["net_return"])
        ),
        combined_improvement_vs_track_a=(
            float(combined_metrics["net_return"]) - float(track_a_metrics["net_return"])
        ),
    )
    return _AssetEvidence(
        public=public,
        train=train,
        v05_curve=v05_curve,
        track_a_curve=track_a_curve,
        combined_curve=combined_curve,
    )


def _curve_metrics(
    evidence: list[_AssetEvidence],
    weights: dict[str, float],
    curve_name: str,
    metrics_name: str,
    starting_cash: float,
) -> dict[str, float | int]:
    size = max((len(getattr(item, curve_name)) for item in evidence), default=1)
    curve = [starting_cash * max(0.0, 1.0 - sum(weights.values()))] * size
    for item in evidence:
        weight = weights.get(item.public.symbol, 0.0)
        if weight <= 0:
            continue
        asset_curve = _pad_curve(getattr(item, curve_name), size)
        curve = [total + weight * value for total, value in zip(curve, asset_curve)]
    ending_cash = curve[-1]
    net_return = (ending_cash - starting_cash) / starting_cash
    drawdown = max_drawdown(curve)
    returns = period_returns(curve)
    return {
        "net_return": net_return,
        "ending_cash": ending_cash,
        "max_drawdown": drawdown,
        "trades": sum(
            int(getattr(item.public, metrics_name)["trades"])
            for item in evidence
            if item.public.symbol in weights
        ),
        "sharpe_ratio": sharpe_ratio(returns, 365),
        "sortino_ratio": sortino_ratio(returns, 365),
        "calmar_ratio": net_return / drawdown if drawdown > 0 else 0.0,
        "exposure": sum(weights.values()),
    }


def _portfolio_periods(
    evidence: list[_AssetEvidence],
    config: CombinedAblationConfig,
) -> list[CombinedPortfolioPeriod]:
    grouped: dict[int, list[_AssetEvidence]] = {}
    for item in evidence:
        grouped.setdefault(item.public.period, []).append(item)
    allocation_config = _allocation_config(config)
    risk_multiplier = 1.0
    previous_drawdown = 0.0
    output: list[CombinedPortfolioPeriod] = []
    for period_index in sorted(grouped):
        current = grouped[period_index]
        if output:
            risk_multiplier = next_drawdown_multiplier(
                risk_multiplier,
                previous_drawdown,
                allocation_config,
            )
        selected, rejected = correlation_filter(
            [
                (
                    item.public.symbol,
                    item.public.consensus_strength,
                    _close_returns(item.train),
                )
                for item in current
                if item.public.combined_deployed
            ],
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
        max_total = config.max_total_exposure * risk_multiplier
        weights: dict[str, float] = {}
        if raw_total > 0:
            weights = {
                symbol: min(
                    config.max_asset_exposure,
                    max_total * score / raw_total,
                )
                for symbol, score in raw_scores.items()
            }
            allocated = sum(weights.values())
            if allocated > max_total:
                scale = max_total / allocated
                weights = {symbol: weight * scale for symbol, weight in weights.items()}
        v05_metrics = _curve_metrics(
            current,
            weights,
            "v05_curve",
            "v05_metrics",
            config.starting_cash,
        )
        track_a_metrics = _curve_metrics(
            current,
            weights,
            "track_a_curve",
            "track_a_metrics",
            config.starting_cash,
        )
        combined_metrics = _curve_metrics(
            current,
            weights,
            "combined_curve",
            "combined_metrics",
            config.starting_cash,
        )
        previous_drawdown = float(combined_metrics["max_drawdown"])
        first = current[0].public
        output.append(
            CombinedPortfolioPeriod(
                period=period_index,
                unseen_start=first.unseen_start,
                unseen_end=first.unseen_end,
                selected_symbols=sorted(weights),
                correlation_rejections=sorted(rejected),
                asset_weights=weights,
                cash_weight=max(0.0, 1.0 - sum(weights.values())),
                risk_multiplier=risk_multiplier,
                v05_metrics=v05_metrics,
                track_a_metrics=track_a_metrics,
                combined_metrics=combined_metrics,
                combined_improvement_vs_v05=(
                    float(combined_metrics["net_return"])
                    - float(v05_metrics["net_return"])
                ),
                combined_improvement_vs_track_a=(
                    float(combined_metrics["net_return"])
                    - float(track_a_metrics["net_return"])
                ),
            )
        )
    return output


def evaluate_combined_ablation(
    folder: str | Path,
    market: Market = Market.CRYPTO,
    config: CombinedAblationConfig | None = None,
) -> CombinedAblationReport:
    config = config or CombinedAblationConfig()
    histories = load_fixed_histories(folder)
    evidence: list[_AssetEvidence] = []
    for symbol in REQUIRED_SYMBOLS:
        windows = independent_train_test_windows(
            histories[symbol],
            config.train_size,
            config.test_size,
        )
        for period_index, (train, unseen) in enumerate(windows, start=1):
            evidence.append(
                _evaluate_asset_period(
                    symbol,
                    market,
                    period_index,
                    train,
                    unseen,
                    histories,
                    config,
                )
            )
    periods = [item.public for item in evidence]
    portfolio_periods = _portfolio_periods(evidence, config)

    part_b = evaluate_meta_allocation(
        folder,
        market=market,
        config=_allocation_config(config),
    )
    part_b_candidates = [
        (item.strategy, item.portfolio_average_return)
        for item in part_b.strategies
    ]
    part_b_best_strategy, part_b_best_return = max(
        part_b_candidates,
        key=lambda item: item[1],
    ) if part_b_candidates else (None, 0.0)

    v05_returns = [float(item.v05_metrics["net_return"]) for item in periods]
    track_a_returns = [float(item.track_a_metrics["net_return"]) for item in periods]
    combined_returns = [float(item.combined_metrics["net_return"]) for item in periods]
    deployed = [item for item in periods if item.combined_deployed]
    positive_fraction = (
        sum(float(item.combined_metrics["net_return"]) > 0 for item in deployed)
        / len(deployed)
        if deployed
        else 0.0
    )
    asset_averages = {
        symbol: mean(
            float(item.combined_metrics["net_return"])
            for item in periods
            if item.symbol == symbol
        )
        for symbol in REQUIRED_SYMBOLS
    }
    profitable_assets = sorted(
        symbol for symbol, value in asset_averages.items() if value > 0
    )
    v05_average = mean(v05_returns) if v05_returns else 0.0
    track_a_average = mean(track_a_returns) if track_a_returns else 0.0
    combined_average = mean(combined_returns) if combined_returns else 0.0
    combined_worst_drawdown = max(
        (float(item.combined_metrics["max_drawdown"]) for item in periods),
        default=0.0,
    )

    v05_portfolio = [float(item.v05_metrics["net_return"]) for item in portfolio_periods]
    track_a_portfolio = [
        float(item.track_a_metrics["net_return"])
        for item in portfolio_periods
    ]
    combined_portfolio = [
        float(item.combined_metrics["net_return"])
        for item in portfolio_periods
    ]
    v05_portfolio_average = mean(v05_portfolio) if v05_portfolio else 0.0
    track_a_portfolio_average = mean(track_a_portfolio) if track_a_portfolio else 0.0
    combined_portfolio_average = mean(combined_portfolio) if combined_portfolio else 0.0
    portfolio_worst_drawdown = max(
        (float(item.combined_metrics["max_drawdown"]) for item in portfolio_periods),
        default=0.0,
    )

    reasons: list[str] = []
    if len(deployed) < config.min_deployed_periods:
        reasons.append("fewer_than_six_combined_unseen_deployments")
    if combined_average <= 0:
        reasons.append("combined_average_unseen_return_not_positive")
    if combined_average <= track_a_average:
        reasons.append("combined_did_not_improve_over_track_a_alone")
    if combined_average <= v05_average:
        reasons.append("combined_did_not_improve_over_frozen_v05")
    if positive_fraction < config.min_positive_deployed_fraction:
        reasons.append("fewer_than_half_of_combined_deployments_profitable")
    if len(profitable_assets) < config.min_profitable_assets:
        reasons.append("combined_positive_results_depend_on_fewer_than_two_assets")
    if combined_worst_drawdown > config.max_drawdown:
        reasons.append("combined_asset_drawdown_too_high")
    if combined_portfolio_average <= 0:
        reasons.append("combined_portfolio_average_return_not_positive")
    if combined_portfolio_average <= track_a_portfolio_average:
        reasons.append("combined_portfolio_did_not_improve_over_track_a")
    if combined_portfolio_average <= part_b_best_return:
        reasons.append("combined_portfolio_did_not_improve_over_part_b")
    if portfolio_worst_drawdown > config.max_drawdown:
        reasons.append("combined_portfolio_drawdown_too_high")

    return CombinedAblationReport(
        schema_version=COMBINED_SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        market=market.value,
        dataset_fingerprint=dataset_fingerprint(histories),
        symbols=list(REQUIRED_SYMBOLS),
        family=COMBINED_FAMILY,
        config=asdict(config),
        periods=periods,
        portfolio_periods=portfolio_periods,
        track_a_average_return=track_a_average,
        combined_average_return=combined_average,
        v05_average_return=v05_average,
        part_b_best_strategy=part_b_best_strategy,
        part_b_best_portfolio_average_return=part_b_best_return,
        combined_average_improvement_vs_track_a=combined_average - track_a_average,
        combined_average_improvement_vs_v05=combined_average - v05_average,
        combined_deployed_periods=len(deployed),
        combined_positive_deployed_fraction=positive_fraction,
        combined_worst_drawdown=combined_worst_drawdown,
        combined_asset_average_returns=asset_averages,
        combined_profitable_assets=profitable_assets,
        track_a_portfolio_average_return=track_a_portfolio_average,
        combined_portfolio_average_return=combined_portfolio_average,
        v05_portfolio_average_return=v05_portfolio_average,
        combined_portfolio_improvement_vs_track_a=(
            combined_portfolio_average - track_a_portfolio_average
        ),
        combined_portfolio_improvement_vs_v05=(
            combined_portfolio_average - v05_portfolio_average
        ),
        combined_portfolio_worst_drawdown=portfolio_worst_drawdown,
        accepted=not reasons,
        reasons=(
            reasons
            or [
                "Relative-strength signals plus meta-allocation passed every frozen ablation gate."
            ]
        ),
    )


def write_combined_report(path: str | Path, report: CombinedAblationReport) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    temporary.replace(output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the frozen v0.6 ablation: v0.5, Track A relative strength, "
            "Part B old-signal allocation, and Track A plus Part B."
        )
    )
    parser.add_argument("--folder", required=True)
    parser.add_argument(
        "--market",
        choices=[item.value for item in Market],
        default=Market.CRYPTO.value,
    )
    parser.add_argument("--json-out", default="reports/combined_ablation_v06.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = evaluate_combined_ablation(
        args.folder,
        market=Market(args.market),
    )
    write_combined_report(args.json_out, report)
    print(json.dumps(asdict(report), indent=2))
    return 0 if report.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
