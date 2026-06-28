from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Iterable

from tradebot.backtest.paper_trader import PaperTrader
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
    min_train_net_return: float = -0.02
    min_test_net_return: float = -0.03
    overfit_gap_limit: float = 0.08
    train_weight_net_return: float = 0.45
    train_weight_win_rate: float = 0.25
    train_weight_drawdown: float = 0.20
    train_weight_trade_count: float = 0.10


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
    windows: list[tuple[list[Candle], list[Candle]]] = []
    start = 0
    while start + train_size + test_size <= len(candles):
        train = candles[start : start + train_size]
        test = candles[start + train_size : start + train_size + test_size]
        windows.append((train, test))
        start += test_size
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
        "max_drawdown": result.max_drawdown,
        "win_rate": result.win_rate,
        "trades": len(result.trades),
        "total_fees": result.total_fees,
        "total_tax": result.total_tax,
        "ending_cash": result.ending_cash,
    }


def selection_score(metrics: dict[str, float | int], config: WalkForwardConfig) -> float:
    trade_score = min(float(metrics["trades"]) / max(config.min_trades, 1), 2.0) / 2.0
    drawdown_score = max(0.0, 1.0 - float(metrics["max_drawdown"]) / max(config.max_drawdown, 1e-9))
    return (
        float(metrics["net_return"]) * config.train_weight_net_return
        + float(metrics["win_rate"]) * config.train_weight_win_rate
        + drawdown_score * config.train_weight_drawdown
        + trade_score * config.train_weight_trade_count
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
    if float(test_metrics["net_return"]) < config.min_test_net_return:
        reasons.append("weak_test_net_profit_after_cost_tax")
    if float(train_metrics["net_return"]) - float(test_metrics["net_return"]) > config.overfit_gap_limit:
        reasons.append("train_test_overfit_gap")
    if float(train_metrics["net_return"]) > 0 and float(test_metrics["net_return"]) < 0:
        reasons.append("profitable_train_failed_unseen_test")
    return reasons


def select_best_parameters(
    symbol: str,
    market: Market,
    train: list[Candle],
    strategy_name: str,
    parameter_sets: Iterable[dict[str, Any]],
    config: WalkForwardConfig,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for params in parameter_sets:
        result = PaperTrader(market, build_strategy(strategy_name, params)).run(symbol, train)
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
    parameter_sets = parameter_grid(grids[strategy_name])
    windows = split_windows(candles, config.train_size, config.test_size)
    split_results: list[dict[str, Any]] = []

    for index, (train, test) in enumerate(windows, start=1):
        selection = select_best_parameters(symbol, market, train, strategy_name, parameter_sets, config)
        selected_params = selection["selected"]["params"]
        test_result = PaperTrader(market, build_strategy(strategy_name, selected_params)).run(symbol, test)
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
                "train_candidates": selection["candidates"],
                "accepted": not reasons,
                "rejection_reasons": reasons,
            }
        )

    accepted = [row for row in split_results if row["accepted"]]
    stability_score = len(accepted) / len(split_results) if split_results else 0.0
    reason = "Stable across walk-forward splits" if stability_score >= 0.5 else "Rejected: selected parameters were not stable on unseen test windows"
    return WalkForwardResult(split_results, stability_score, stability_score >= 0.5 and bool(split_results), reason)


def _strategy_name_from_instance(strategy: Strategy | None) -> str:
    if isinstance(strategy, MomentumVolumeStrategy):
        return "momentum"
    if isinstance(strategy, BreakoutStrategy):
        return "breakout"
    if isinstance(strategy, MeanReversionStrategy):
        return "mean_reversion"
    return "momentum"
