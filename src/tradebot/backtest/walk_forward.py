from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Iterable

from tradebot.backtest.paper_trader import BacktestConfig, PaperTrader
from tradebot.models import BacktestResult, Candle, Market, WalkForwardResult
from tradebot.strategies.base import Strategy
from tradebot.strategies.breakout import BreakoutStrategy
from tradebot.strategies.mean_reversion import MeanReversionStrategy
from tradebot.strategies.momentum import MomentumVolumeStrategy


@dataclass(frozen=True)
class WalkForwardConfig:
    train_size: int = 30
    test_size: int = 15
    min_trades: int = 1
    max_drawdown: float = 0.25
    min_train_net_return: float = 0.0
    min_test_net_return: float = 0.0
    overfit_gap_limit: float = 0.08
    min_stability_score: float = 0.67
    require_all_unseen_positive: bool = True
    max_trades_per_100_bars: float = 10.0
    max_cost_drag_ratio: float = 0.50
    train_weight_net_return: float = 0.45
    train_weight_win_rate: float = 0.15
    train_weight_drawdown: float = 0.20
    train_weight_trade_count: float = 0.05
    train_weight_excess_return: float = 0.15

    def __post_init__(self) -> None:
        if self.train_size <= 0 or self.test_size <= 0:
            raise ValueError("train_size and test_size must be positive")
        if self.min_trades < 0:
            raise ValueError("min_trades cannot be negative")
        if not 0 < self.max_drawdown < 1:
            raise ValueError("max_drawdown must be between 0 and 1")
        if not 0 <= self.min_stability_score <= 1:
            raise ValueError("min_stability_score must be between 0 and 1")
        if self.max_trades_per_100_bars <= 0:
            raise ValueError("max_trades_per_100_bars must be positive")
        if self.max_cost_drag_ratio < 0:
            raise ValueError("max_cost_drag_ratio cannot be negative")


DEFAULT_PARAMETER_GRIDS: dict[str, dict[str, list[Any]]] = {
    "momentum": {
        "lookback": [3, 5, 8],
        "min_return": [0.008, 0.015, 0.025],
        "volume_multiplier": [1.0, 1.15, 1.35],
    },
    "breakout": {
        "lookback": [5, 10, 15],
        "buffer": [0.0, 0.002, 0.005],
    },
    "mean_reversion": {
        "lookback": [5, 10, 15],
        "threshold": [0.015, 0.025, 0.04],
    },
}


def split_windows(candles: list[Candle], train_size: int, test_size: int) -> list[tuple[list[Candle], list[Candle]]]:
    if train_size <= 0 or test_size <= 0:
        raise ValueError("train_size and test_size must be positive")
    windows: list[tuple[list[Candle], list[Candle]]] = []
    test_start = train_size
    while test_start + test_size <= len(candles):
        train = candles[test_start - train_size : test_start]
        test = candles[test_start : test_start + test_size]
        windows.append((train, test))
        test_start += test_size
    return windows


def parameter_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(grid)
    if not keys:
        return [{}]
    return [dict(zip(keys, values)) for values in product(*(grid[key] for key in keys))]


def build_strategy(strategy_name: str, params: dict[str, Any] | None = None) -> Strategy:
    params = params or {}
    if strategy_name == "momentum":
        return MomentumVolumeStrategy(**params)
    if strategy_name == "breakout":
        return BreakoutStrategy(**params)
    if strategy_name == "mean_reversion":
        return MeanReversionStrategy(**params)
    raise ValueError(f"Unknown strategy for walk-forward grid: {strategy_name}")


def result_metrics(result: BacktestResult) -> dict[str, float | int]:
    return {
        "net_return": result.net_return,
        "gross_return": result.gross_return,
        "cash_return": result.cash_return,
        "buy_and_hold_return": result.buy_and_hold_return,
        "excess_return": result.excess_return,
        "max_drawdown": result.max_drawdown,
        "win_rate": result.win_rate,
        "trades": len(result.trades),
        "trades_per_100_bars": result.trades_per_100_bars,
        "average_holding_bars": result.average_holding_bars,
        "turnover": result.turnover,
        "cost_drag_ratio": result.cost_drag_ratio,
        "total_fees": result.total_fees,
        "total_tax": result.total_tax,
        "ending_cash": result.ending_cash,
        "sharpe_ratio": result.sharpe_ratio,
        "profit_factor": result.profit_factor,
    }


def selection_score(metrics: dict[str, float | int], config: WalkForwardConfig) -> float:
    trade_score = min(float(metrics["trades"]) / max(config.min_trades, 1), 2.0) / 2.0
    drawdown_score = max(0.0, 1.0 - float(metrics["max_drawdown"]) / max(config.max_drawdown, 1e-9))
    churn_penalty = max(
        0.0,
        float(metrics.get("trades_per_100_bars", 0.0)) / config.max_trades_per_100_bars - 1.0,
    )
    cost_penalty = max(
        0.0,
        float(metrics.get("cost_drag_ratio", 0.0)) / max(config.max_cost_drag_ratio, 1e-9) - 1.0,
    )
    return (
        float(metrics["net_return"]) * config.train_weight_net_return
        + float(metrics.get("excess_return", 0.0)) * config.train_weight_excess_return
        + float(metrics["win_rate"]) * config.train_weight_win_rate
        + drawdown_score * config.train_weight_drawdown
        + trade_score * config.train_weight_trade_count
        - churn_penalty * 0.10
        - cost_penalty * 0.10
    )


def rejection_reasons(
    train_metrics: dict[str, float | int],
    test_metrics: dict[str, float | int],
    config: WalkForwardConfig,
) -> list[str]:
    reasons: list[str] = []
    if int(train_metrics["trades"]) < config.min_trades:
        reasons.append("too_few_train_trades")
    if int(test_metrics["trades"]) < config.min_trades:
        reasons.append("too_few_test_trades")
    if float(train_metrics["max_drawdown"]) > config.max_drawdown:
        reasons.append("high_train_drawdown")
    if float(test_metrics["max_drawdown"]) > config.max_drawdown:
        reasons.append("high_test_drawdown")
    if float(train_metrics["net_return"]) < config.min_train_net_return:
        reasons.append("weak_train_net_profit_after_cost_tax")
    if float(test_metrics["net_return"]) <= config.min_test_net_return:
        reasons.append("non_positive_unseen_net_return")
    if float(train_metrics["net_return"]) - float(test_metrics["net_return"]) > config.overfit_gap_limit:
        reasons.append("train_test_overfit_gap")
    if float(train_metrics["net_return"]) > 0 and float(test_metrics["net_return"]) <= 0:
        reasons.append("profitable_train_failed_unseen_test")
    if float(test_metrics.get("trades_per_100_bars", 0.0)) > config.max_trades_per_100_bars:
        reasons.append("unseen_overtrading")
    if float(test_metrics.get("cost_drag_ratio", 0.0)) > config.max_cost_drag_ratio:
        reasons.append("unseen_transaction_cost_drag")
    return reasons


def select_best_parameters(
    symbol: str,
    market: Market,
    train: list[Candle],
    strategy_name: str,
    parameter_sets: Iterable[dict[str, Any]],
    config: WalkForwardConfig,
    backtest_config: BacktestConfig | None = None,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for params in parameter_sets:
        result = PaperTrader(
            market,
            build_strategy(strategy_name, params),
            config=backtest_config,
        ).run(symbol, train)
        metrics = result_metrics(result)
        candidates.append(
            {
                "params": params,
                "metrics": metrics,
                "selection_score": selection_score(metrics, config),
            }
        )
    if not candidates:
        raise ValueError("Walk-forward parameter grid produced no candidates")
    candidates.sort(key=lambda row: row["selection_score"], reverse=True)
    return {"selected": candidates[0], "candidates": candidates}


def walk_forward(
    symbol: str,
    market: Market,
    candles: list[Candle],
    strategy: Strategy | None = None,
    train_size: int | None = None,
    test_size: int | None = None,
    strategy_name: str | None = None,
    parameter_grids: dict[str, dict[str, list[Any]]] | None = None,
    config: WalkForwardConfig | None = None,
    backtest_config: BacktestConfig | None = None,
) -> WalkForwardResult:
    config = config or WalkForwardConfig()
    if train_size is not None or test_size is not None:
        config = WalkForwardConfig(
            **{
                **config.__dict__,
                "train_size": train_size or config.train_size,
                "test_size": test_size or config.test_size,
            }
        )
    if strategy_name is None:
        strategy_name = _strategy_name_from_instance(strategy) if strategy is not None else "momentum"

    grids = parameter_grids or DEFAULT_PARAMETER_GRIDS
    if strategy_name not in grids:
        raise ValueError(f"No parameter grid configured for strategy: {strategy_name}")
    parameter_sets = parameter_grid(grids[strategy_name])
    windows = split_windows(candles, config.train_size, config.test_size)
    split_results: list[dict[str, Any]] = []

    for index, (train, test) in enumerate(windows, start=1):
        selection = select_best_parameters(
            symbol,
            market,
            train,
            strategy_name,
            parameter_sets,
            config,
            backtest_config=backtest_config,
        )
        selected_params = selection["selected"]["params"]

        lookback = int(selected_params.get("lookback", 10))
        warmup_count = min(len(train), max(10, lookback + 1))
        evaluation_candles = [*train[-warmup_count:], *test]
        test_result = PaperTrader(
            market,
            build_strategy(strategy_name, selected_params),
            config=backtest_config,
        ).run(
            symbol,
            evaluation_candles,
            trade_start_index=warmup_count,
        )
        test_metrics = result_metrics(test_result)
        train_metrics = selection["selected"]["metrics"]
        reasons = rejection_reasons(train_metrics, test_metrics, config)
        split_results.append(
            {
                "split": index,
                "strategy": strategy_name,
                "train_start": train[0].timestamp.isoformat(),
                "train_end": train[-1].timestamp.isoformat(),
                "test_start": test[0].timestamp.isoformat(),
                "test_end": test[-1].timestamp.isoformat(),
                "selected_parameters": selected_params,
                "train_metrics": train_metrics,
                "test_metrics": test_metrics,
                "cash_benchmark_return": 0.0,
                "buy_and_hold_benchmark_return": test_metrics["buy_and_hold_return"],
                "train_candidates": selection["candidates"],
                "accepted": not reasons,
                "rejection_reasons": reasons,
            }
        )

    accepted = [row for row in split_results if row["accepted"]]
    stability_score = len(accepted) / len(split_results) if split_results else 0.0
    all_positive = bool(split_results) and all(
        float(row["test_metrics"]["net_return"]) > config.min_test_net_return
        for row in split_results
    )
    accepted_overall = (
        bool(split_results)
        and stability_score >= config.min_stability_score
        and (all_positive or not config.require_all_unseen_positive)
    )
    if not split_results:
        reason = "Rejected: no independent unseen windows could be created"
    elif not all_positive and config.require_all_unseen_positive:
        reason = "Rejected: at least one unseen window failed to produce a positive net return"
    elif stability_score < config.min_stability_score:
        reason = "Rejected: selected parameters were not stable across enough unseen windows"
    else:
        reason = "Passed: positive, cost-aware performance was stable across independent unseen windows"
    return WalkForwardResult(split_results, stability_score, accepted_overall, reason)


def _strategy_name_from_instance(strategy: Strategy | None) -> str:
    if isinstance(strategy, MomentumVolumeStrategy):
        return "momentum"
    if isinstance(strategy, BreakoutStrategy):
        return "breakout"
    if isinstance(strategy, MeanReversionStrategy):
        return "mean_reversion"
    return "momentum"
