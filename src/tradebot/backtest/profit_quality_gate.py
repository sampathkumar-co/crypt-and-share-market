from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from statistics import mean
from typing import Any

from tradebot.backtest.paper_trader import PaperTrader
from tradebot.backtest.research_gate import (
    EXECUTION_PROFILES,
    ResearchGateConfig,
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
    temporal_fold_ranges,
    with_stability_flag,
)
from tradebot.backtest.walk_forward import (
    DEFAULT_PARAMETER_GRIDS,
    build_strategy,
    parameter_grid,
    result_metrics,
)
from tradebot.models import Candle, Market


STRATEGIES = ("momentum", "breakout", "mean_reversion")
PROFIT_QUALITY_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class ProfitQualityConfig:
    train_size: int = 180
    test_size: int = 60
    max_candidates_per_strategy: int = 120
    stability_screen_candidates: int = 24
    min_deployed_periods: int = 3
    min_positive_deployed_fraction: float = 0.60
    min_stable_beats_naive_fraction: float = 0.50
    min_average_return_improvement: float = 0.0
    max_stable_drawdown: float = 0.20
    starting_cash: float = 100000.0
    stability_policy: TemporalStabilityPolicy = field(default_factory=TemporalStabilityPolicy)

    def __post_init__(self) -> None:
        if self.train_size < 30 or self.test_size < 10:
            raise ValueError("train_size must be >= 30 and test_size must be >= 10")
        if self.max_candidates_per_strategy < 1:
            raise ValueError("max_candidates_per_strategy must be positive")
        if not 1 <= self.stability_screen_candidates <= self.max_candidates_per_strategy:
            raise ValueError("stability_screen_candidates must be within the candidate budget")
        if self.min_deployed_periods < 1:
            raise ValueError("min_deployed_periods must be positive")
        if not 0 <= self.min_positive_deployed_fraction <= 1:
            raise ValueError("min_positive_deployed_fraction must be between 0 and 1")
        if not 0 <= self.min_stable_beats_naive_fraction <= 1:
            raise ValueError("min_stable_beats_naive_fraction must be between 0 and 1")
        if not 0 < self.max_stable_drawdown < 1:
            raise ValueError("max_stable_drawdown must be between 0 and 1")
        if self.starting_cash <= 0:
            raise ValueError("starting_cash must be positive")


@dataclass
class ProfitQualityPeriod:
    symbol: str
    strategy: str
    period: int
    train_start: str
    train_end: str
    unseen_start: str
    unseen_end: str
    stable_abstained: bool
    stable_selection_reasons: list[str]
    stable_parameters: dict[str, Any]
    stable_execution: dict[str, Any]
    stable_training_stability: dict[str, float | int | bool]
    naive_parameters: dict[str, Any]
    naive_execution: dict[str, Any]
    stable_metrics: dict[str, float | int]
    naive_metrics: dict[str, float | int]
    net_return_improvement: float
    drawdown_improvement: float


@dataclass
class StrategyProfitQualityResult:
    strategy: str
    approved: bool
    reasons: list[str]
    periods: list[ProfitQualityPeriod]
    deployed_periods: int
    abstained_periods: int
    average_stable_return: float
    average_naive_return: float
    average_net_improvement: float
    positive_deployed_fraction: float
    stable_beats_naive_fraction: float
    worst_stable_drawdown: float


@dataclass
class ProfitQualityReport:
    schema_version: str
    generated_at: str
    market: str
    dataset_fingerprint: str
    symbols: list[str]
    config: dict[str, Any]
    strategies: list[StrategyProfitQualityResult]
    accepted: bool
    champion_strategy: str | None
    reasons: list[str]
    paper_only: bool = True
    authorizes_real_trading: bool = False


def _candidate_grid(strategy_name: str, config: ProfitQualityConfig) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    strategy_params = parameter_grid(DEFAULT_PARAMETER_GRIDS[strategy_name])
    return list(product(strategy_params, EXECUTION_PROFILES))[: config.max_candidates_per_strategy]


def _gate_score_config(config: ProfitQualityConfig) -> ResearchGateConfig:
    return ResearchGateConfig(max_candidates_per_strategy=config.max_candidates_per_strategy)


def _run_candidate(
    symbol: str,
    market: Market,
    candles: list[Candle],
    strategy_name: str,
    strategy_params: dict[str, Any],
    execution_profile: dict[str, Any],
    starting_cash: float,
    *,
    trade_start_index: int = 0,
) -> dict[str, float | int]:
    lookback = int(strategy_params.get("lookback", 10))
    result = PaperTrader(
        market,
        build_strategy(strategy_name, strategy_params),
        starting_cash=starting_cash,
        config=_execution_config(execution_profile, warmup_bars=lookback + 1),
    ).run(symbol, candles, trade_start_index=trade_start_index)
    return result_metrics(result)


def _fold_metrics(
    symbol: str,
    market: Market,
    train: list[Candle],
    strategy_name: str,
    strategy_params: dict[str, Any],
    execution_profile: dict[str, Any],
    config: ProfitQualityConfig,
) -> list[dict[str, float | int]]:
    lookback = int(strategy_params.get("lookback", 10))
    warmup = max(10, lookback + 1)
    results: list[dict[str, float | int]] = []
    for fold_start, fold_end in temporal_fold_ranges(len(train), config.stability_policy):
        history_start = max(0, fold_start - warmup)
        history = train[history_start:fold_end]
        trade_start = max(0, fold_start - history_start)
        results.append(
            _run_candidate(
                symbol,
                market,
                history,
                strategy_name,
                strategy_params,
                execution_profile,
                config.starting_cash,
                trade_start_index=trade_start,
            )
        )
    return results


def _select_candidates(
    symbol: str,
    market: Market,
    train: list[Candle],
    strategy_name: str,
    config: ProfitQualityConfig,
) -> dict[str, Any]:
    gate_config = _gate_score_config(config)
    candidates: list[dict[str, Any]] = []
    for strategy_params, execution_profile in _candidate_grid(strategy_name, config):
        metrics = _run_candidate(
            symbol,
            market,
            train,
            strategy_name,
            strategy_params,
            execution_profile,
            config.starting_cash,
        )
        candidates.append(
            {
                "strategy_parameters": strategy_params,
                "execution_parameters": execution_profile,
                "train_metrics": metrics,
                "base_score": _candidate_score(metrics, gate_config),
            }
        )
    if not candidates:
        raise ValueError(f"No candidates generated for {strategy_name}")

    candidates.sort(key=lambda item: float(item["base_score"]), reverse=True)
    naive = candidates[0]
    screened = candidates[: config.stability_screen_candidates]
    stable_candidates: list[dict[str, Any]] = []
    screened_diagnostics: list[dict[str, Any]] = []
    for candidate in screened:
        folds = _fold_metrics(
            symbol,
            market,
            train,
            strategy_name,
            candidate["strategy_parameters"],
            candidate["execution_parameters"],
            config,
        )
        summary = summarize_fold_metrics(folds)
        reasons = stability_reasons(candidate["train_metrics"], summary, config.stability_policy)
        summary = with_stability_flag(summary, reasons)
        candidate = {
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
        screened_diagnostics.append(candidate)
        if not reasons:
            stable_candidates.append(candidate)

    stable_candidates.sort(key=lambda item: float(item["stability_score"]), reverse=True)
    best_screened = max(
        screened_diagnostics,
        key=lambda item: float(item["stability_score"]),
    )
    return {
        "naive": naive,
        "stable": stable_candidates[0] if stable_candidates else None,
        "abstention_reasons": list(best_screened.get("selection_reasons", [])),
        "best_screened": best_screened,
        "candidate_count": len(candidates),
        "screened_count": len(screened),
    }


def _cash_metrics(unseen: list[Candle], starting_cash: float) -> dict[str, float | int]:
    first_open = unseen[0].open if unseen else 0.0
    buy_and_hold = (
        (unseen[-1].close - first_open) / first_open
        if unseen and first_open > 0
        else 0.0
    )
    return {
        "net_return": 0.0,
        "gross_return": 0.0,
        "cash_return": 0.0,
        "buy_and_hold_return": buy_and_hold,
        "excess_return": -buy_and_hold,
        "max_drawdown": 0.0,
        "win_rate": 0.0,
        "trades": 0,
        "trades_per_100_bars": 0.0,
        "average_holding_bars": 0.0,
        "turnover": 0.0,
        "cost_drag_ratio": 0.0,
        "total_fees": 0.0,
        "total_tax": 0.0,
        "ending_cash": starting_cash,
        "sharpe_ratio": 0.0,
        "profit_factor": 0.0,
    }


def _evaluate_unseen(
    symbol: str,
    market: Market,
    train: list[Candle],
    unseen: list[Candle],
    strategy_name: str,
    candidate: dict[str, Any],
    config: ProfitQualityConfig,
) -> dict[str, float | int]:
    strategy_params = candidate["strategy_parameters"]
    execution_profile = candidate["execution_parameters"]
    lookback = int(strategy_params.get("lookback", 10))
    warmup_count = min(len(train), max(30, lookback + 1))
    evaluation = [*train[-warmup_count:], *unseen]
    return _run_candidate(
        symbol,
        market,
        evaluation,
        strategy_name,
        strategy_params,
        execution_profile,
        config.starting_cash,
        trade_start_index=warmup_count,
    )


def _evaluate_period(
    symbol: str,
    market: Market,
    strategy_name: str,
    period_index: int,
    train: list[Candle],
    unseen: list[Candle],
    config: ProfitQualityConfig,
) -> ProfitQualityPeriod:
    selection = _select_candidates(symbol, market, train, strategy_name, config)
    naive = selection["naive"]
    stable = selection["stable"]
    naive_metrics = _evaluate_unseen(symbol, market, train, unseen, strategy_name, naive, config)
    if stable is None:
        stable_metrics = _cash_metrics(unseen, config.starting_cash)
        stable_parameters: dict[str, Any] = {}
        stable_execution: dict[str, Any] = {}
        stability = selection["best_screened"].get("training_stability", {})
    else:
        stable_metrics = _evaluate_unseen(symbol, market, train, unseen, strategy_name, stable, config)
        stable_parameters = stable["strategy_parameters"]
        stable_execution = stable["execution_parameters"]
        stability = stable["training_stability"]

    return ProfitQualityPeriod(
        symbol=symbol,
        strategy=strategy_name,
        period=period_index,
        train_start=train[0].timestamp.isoformat(),
        train_end=train[-1].timestamp.isoformat(),
        unseen_start=unseen[0].timestamp.isoformat(),
        unseen_end=unseen[-1].timestamp.isoformat(),
        stable_abstained=stable is None,
        stable_selection_reasons=selection["abstention_reasons"] if stable is None else [],
        stable_parameters=stable_parameters,
        stable_execution=stable_execution,
        stable_training_stability=stability,
        naive_parameters=naive["strategy_parameters"],
        naive_execution=naive["execution_parameters"],
        stable_metrics=stable_metrics,
        naive_metrics=naive_metrics,
        net_return_improvement=float(stable_metrics["net_return"]) - float(naive_metrics["net_return"]),
        drawdown_improvement=float(naive_metrics["max_drawdown"]) - float(stable_metrics["max_drawdown"]),
    )


def _summarize_strategy(
    strategy_name: str,
    periods: list[ProfitQualityPeriod],
    config: ProfitQualityConfig,
) -> StrategyProfitQualityResult:
    deployed = [period for period in periods if not period.stable_abstained]
    stable_returns = [float(period.stable_metrics["net_return"]) for period in periods]
    naive_returns = [float(period.naive_metrics["net_return"]) for period in periods]
    improvements = [period.net_return_improvement for period in periods]
    positive_fraction = (
        sum(float(period.stable_metrics["net_return"]) > 0 for period in deployed) / len(deployed)
        if deployed
        else 0.0
    )
    beats_fraction = (
        sum(period.net_return_improvement > 0 for period in periods) / len(periods)
        if periods
        else 0.0
    )
    worst_drawdown = max(
        (float(period.stable_metrics["max_drawdown"]) for period in periods),
        default=0.0,
    )
    average_stable = mean(stable_returns) if stable_returns else 0.0
    average_naive = mean(naive_returns) if naive_returns else 0.0
    average_improvement = mean(improvements) if improvements else 0.0

    reasons: list[str] = []
    if len(deployed) < config.min_deployed_periods:
        reasons.append("too_few_stable_deployed_periods")
    if average_stable <= 0:
        reasons.append("average_stable_return_not_positive")
    if average_improvement <= config.min_average_return_improvement:
        reasons.append("no_average_improvement_over_naive_selection")
    if positive_fraction < config.min_positive_deployed_fraction:
        reasons.append("too_few_positive_stable_periods")
    if beats_fraction < config.min_stable_beats_naive_fraction:
        reasons.append("stable_selection_did_not_beat_naive_often_enough")
    if worst_drawdown > config.max_stable_drawdown:
        reasons.append("stable_selection_drawdown_too_high")

    return StrategyProfitQualityResult(
        strategy=strategy_name,
        approved=not reasons,
        reasons=reasons or ["Stability-aware selection improved unseen profit quality."],
        periods=periods,
        deployed_periods=len(deployed),
        abstained_periods=len(periods) - len(deployed),
        average_stable_return=average_stable,
        average_naive_return=average_naive,
        average_net_improvement=average_improvement,
        positive_deployed_fraction=positive_fraction,
        stable_beats_naive_fraction=beats_fraction,
        worst_stable_drawdown=worst_drawdown,
    )


def evaluate_profit_quality(
    folder: str | Path,
    market: Market = Market.CRYPTO,
    config: ProfitQualityConfig | None = None,
) -> ProfitQualityReport:
    config = config or ProfitQualityConfig()
    histories = load_histories(folder)
    strategy_results: list[StrategyProfitQualityResult] = []
    for strategy_name in STRATEGIES:
        periods: list[ProfitQualityPeriod] = []
        for symbol, candles in histories.items():
            windows = independent_train_test_windows(candles, config.train_size, config.test_size)
            for period_index, (train, unseen) in enumerate(windows, start=1):
                periods.append(
                    _evaluate_period(
                        symbol,
                        market,
                        strategy_name,
                        period_index,
                        train,
                        unseen,
                        config,
                    )
                )
        strategy_results.append(_summarize_strategy(strategy_name, periods, config))

    approved = [item for item in strategy_results if item.approved]
    champion = (
        max(
            approved,
            key=lambda item: (
                item.average_stable_return,
                item.average_net_improvement,
                -item.worst_stable_drawdown,
            ),
        ).strategy
        if approved
        else None
    )
    accepted = bool(approved)
    reasons = (
        [
            f"Profit-quality gate passed for: {', '.join(item.strategy for item in approved)}.",
            f"Champion for another forward paper comparison: {champion}.",
        ]
        if accepted
        else [
            "No strategy proved that stability-aware selection improves unseen profit quality.",
            "Keep the current paper-only gate closed and continue research.",
        ]
    )
    return ProfitQualityReport(
        schema_version=PROFIT_QUALITY_SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        market=market.value,
        dataset_fingerprint=dataset_fingerprint(histories),
        symbols=sorted(histories),
        config=asdict(config),
        strategies=strategy_results,
        accepted=accepted,
        champion_strategy=champion,
        reasons=reasons,
    )


def write_profit_quality_report(path: str | Path, report: ProfitQualityReport) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    temporary.replace(output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare stability-aware parameter selection with naive best-training selection."
    )
    parser.add_argument("--folder", required=True)
    parser.add_argument("--market", choices=[item.value for item in Market], default=Market.CRYPTO.value)
    parser.add_argument("--train-size", type=int, default=180)
    parser.add_argument("--test-size", type=int, default=60)
    parser.add_argument("--max-candidates", type=int, default=120)
    parser.add_argument("--screen-candidates", type=int, default=24)
    parser.add_argument("--json-out", default="reports/profit_quality_gate.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = ProfitQualityConfig(
        train_size=args.train_size,
        test_size=args.test_size,
        max_candidates_per_strategy=args.max_candidates,
        stability_screen_candidates=args.screen_candidates,
    )
    report = evaluate_profit_quality(
        args.folder,
        market=Market(args.market),
        config=config,
    )
    write_profit_quality_report(args.json_out, report)
    print(json.dumps(asdict(report), indent=2))
    return 0 if report.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
