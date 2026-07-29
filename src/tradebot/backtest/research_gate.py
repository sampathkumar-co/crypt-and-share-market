from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from statistics import mean, median
from typing import Any

from tradebot.backtest.paper_trader import BacktestConfig, PaperTrader
from tradebot.backtest.walk_forward import (
    DEFAULT_PARAMETER_GRIDS,
    build_strategy,
    parameter_grid,
    result_metrics,
)
from tradebot.data.csv_loader import load_candles
from tradebot.models import Candle, Market


GATE_SCHEMA_VERSION = "1.0"
STRATEGIES = ("momentum", "breakout", "mean_reversion")


@dataclass(frozen=True)
class ResearchGateConfig:
    train_size: int = 180
    test_size: int = 60
    min_independent_periods: int = 3
    min_positive_unseen_return: float = 0.0
    require_all_unseen_positive: bool = True
    max_unseen_drawdown: float = 0.20
    max_cost_drag_ratio: float = 0.50
    max_trades_per_100_bars: float = 8.0
    min_average_holding_bars: float = 2.0
    min_beat_buy_hold_fraction: float = 0.50
    max_candidates_per_strategy: int = 120

    def __post_init__(self) -> None:
        if self.train_size < 30:
            raise ValueError("train_size must be at least 30")
        if self.test_size < 10:
            raise ValueError("test_size must be at least 10")
        if self.min_independent_periods < 1:
            raise ValueError("min_independent_periods must be positive")
        if not 0 < self.max_unseen_drawdown < 1:
            raise ValueError("max_unseen_drawdown must be between 0 and 1")
        if self.max_cost_drag_ratio < 0:
            raise ValueError("max_cost_drag_ratio cannot be negative")
        if self.max_trades_per_100_bars <= 0:
            raise ValueError("max_trades_per_100_bars must be positive")
        if self.min_average_holding_bars < 0:
            raise ValueError("min_average_holding_bars cannot be negative")
        if not 0 <= self.min_beat_buy_hold_fraction <= 1:
            raise ValueError("min_beat_buy_hold_fraction must be between 0 and 1")
        if self.max_candidates_per_strategy < 1:
            raise ValueError("max_candidates_per_strategy must be positive")


@dataclass
class GatePeriodResult:
    symbol: str
    strategy: str
    period: int
    train_start: str
    train_end: str
    unseen_start: str
    unseen_end: str
    selected_parameters: dict[str, Any]
    selected_execution: dict[str, Any]
    train_metrics: dict[str, float | int]
    unseen_metrics: dict[str, float | int]
    cash_return: float
    buy_and_hold_return: float
    excess_vs_cash: float
    excess_vs_buy_and_hold: float
    passed: bool
    rejection_reasons: list[str] = field(default_factory=list)


@dataclass
class StrategyGateResult:
    strategy: str
    passed: bool
    reasons: list[str]
    independent_periods: int
    positive_unseen_fraction: float
    beat_buy_and_hold_fraction: float
    average_unseen_return: float
    median_unseen_return: float
    average_excess_vs_buy_and_hold: float
    worst_unseen_return: float
    worst_drawdown: float
    average_cost_drag_ratio: float
    average_trades_per_100_bars: float
    average_holding_bars: float
    periods: list[GatePeriodResult] = field(default_factory=list)


@dataclass
class ResearchGateReport:
    schema_version: str
    generated_at: str
    market: str
    dataset_fingerprint: str
    symbols: list[str]
    config: dict[str, Any]
    strategies: list[StrategyGateResult]
    accepted: bool
    eligible_for_continuous_paper: bool
    champion_strategy: str | None
    passed_strategies: list[str]
    reasons: list[str]
    paper_only: bool = True


EXECUTION_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "min_holding_bars": 2,
        "max_holding_bars": 30,
        "cooldown_bars": 2,
        "exit_confirmation_bars": 2,
        "trailing_stop_pct": 0.03,
        "breakeven_trigger_pct": 0.02,
        "use_regime_filter": True,
    },
    {
        "min_holding_bars": 5,
        "max_holding_bars": 45,
        "cooldown_bars": 3,
        "exit_confirmation_bars": 2,
        "trailing_stop_pct": 0.04,
        "breakeven_trigger_pct": 0.025,
        "use_regime_filter": True,
    },
    {
        "min_holding_bars": 8,
        "max_holding_bars": 60,
        "cooldown_bars": 5,
        "exit_confirmation_bars": 3,
        "trailing_stop_pct": 0.05,
        "breakeven_trigger_pct": 0.03,
        "use_regime_filter": True,
    },
)


def load_histories(folder: str | Path) -> dict[str, list[Candle]]:
    root = Path(folder)
    histories = {
        path.stem: load_candles(path)
        for path in sorted(root.glob("*.csv"))
    }
    if not histories:
        raise ValueError(f"No CSV histories found in {root}")
    return histories


def dataset_fingerprint(histories: dict[str, list[Candle]]) -> str:
    digest = hashlib.sha256()
    for symbol in sorted(histories):
        digest.update(symbol.encode("utf-8"))
        for candle in histories[symbol]:
            digest.update(
                (
                    f"{candle.timestamp.isoformat()}|{candle.open:.12g}|{candle.high:.12g}|"
                    f"{candle.low:.12g}|{candle.close:.12g}|{candle.volume:.12g}\n"
                ).encode("utf-8")
            )
    return digest.hexdigest()


def independent_train_test_windows(
    candles: list[Candle],
    train_size: int,
    test_size: int,
) -> list[tuple[list[Candle], list[Candle]]]:
    windows: list[tuple[list[Candle], list[Candle]]] = []
    test_start = train_size
    while test_start + test_size <= len(candles):
        train = candles[test_start - train_size : test_start]
        unseen = candles[test_start : test_start + test_size]
        windows.append((train, unseen))
        test_start += test_size
    return windows


def _execution_config(profile: dict[str, Any], *, warmup_bars: int) -> BacktestConfig:
    return BacktestConfig(
        warmup_bars=max(10, warmup_bars),
        intrabar_policy="worst_case",
        **profile,
    )


def _candidate_score(metrics: dict[str, float | int], config: ResearchGateConfig) -> float:
    return (
        float(metrics["net_return"]) * 0.55
        + float(metrics.get("excess_return", 0.0)) * 0.15
        + min(float(metrics.get("sharpe_ratio", 0.0)), 3.0) * 0.03
        - float(metrics["max_drawdown"]) * 0.25
        - max(
            0.0,
            float(metrics.get("trades_per_100_bars", 0.0)) / config.max_trades_per_100_bars - 1.0,
        )
        * 0.10
        - max(
            0.0,
            float(metrics.get("cost_drag_ratio", 0.0)) / max(config.max_cost_drag_ratio, 1e-9) - 1.0,
        )
        * 0.10
    )


def _candidate_grid(strategy_name: str, config: ResearchGateConfig) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    strategy_params = parameter_grid(DEFAULT_PARAMETER_GRIDS[strategy_name])
    candidates = list(product(strategy_params, EXECUTION_PROFILES))
    return candidates[: config.max_candidates_per_strategy]


def _select_on_training(
    symbol: str,
    market: Market,
    train: list[Candle],
    strategy_name: str,
    config: ResearchGateConfig,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for strategy_params, execution_profile in _candidate_grid(strategy_name, config):
        lookback = int(strategy_params.get("lookback", 10))
        backtest_config = _execution_config(execution_profile, warmup_bars=lookback + 1)
        result = PaperTrader(
            market,
            build_strategy(strategy_name, strategy_params),
            config=backtest_config,
        ).run(symbol, train)
        metrics = result_metrics(result)
        candidates.append(
            {
                "strategy_parameters": strategy_params,
                "execution_parameters": execution_profile,
                "train_metrics": metrics,
                "selection_score": _candidate_score(metrics, config),
            }
        )
    if not candidates:
        raise ValueError(f"No training candidates generated for {strategy_name}")
    candidates.sort(key=lambda row: row["selection_score"], reverse=True)
    return {"selected": candidates[0], "candidates": candidates}


def _period_reasons(metrics: dict[str, float | int], config: ResearchGateConfig) -> list[str]:
    reasons: list[str] = []
    if float(metrics["net_return"]) <= config.min_positive_unseen_return:
        reasons.append("unseen_return_not_positive")
    if float(metrics["max_drawdown"]) > config.max_unseen_drawdown:
        reasons.append("unseen_drawdown_too_high")
    if float(metrics.get("cost_drag_ratio", 0.0)) > config.max_cost_drag_ratio:
        reasons.append("transaction_cost_drag_too_high")
    if float(metrics.get("trades_per_100_bars", 0.0)) > config.max_trades_per_100_bars:
        reasons.append("overtrading")
    if int(metrics.get("trades", 0)) > 0 and float(metrics.get("average_holding_bars", 0.0)) < config.min_average_holding_bars:
        reasons.append("average_holding_period_too_short")
    return reasons


def _evaluate_period(
    symbol: str,
    market: Market,
    strategy_name: str,
    period_index: int,
    train: list[Candle],
    unseen: list[Candle],
    config: ResearchGateConfig,
) -> GatePeriodResult:
    selection = _select_on_training(symbol, market, train, strategy_name, config)
    selected = selection["selected"]
    strategy_params = selected["strategy_parameters"]
    execution_profile = selected["execution_parameters"]
    lookback = int(strategy_params.get("lookback", 10))
    warmup_count = min(len(train), max(30, lookback + 1))
    evaluation = [*train[-warmup_count:], *unseen]
    test_result = PaperTrader(
        market,
        build_strategy(strategy_name, strategy_params),
        config=_execution_config(execution_profile, warmup_bars=lookback + 1),
    ).run(symbol, evaluation, trade_start_index=warmup_count)
    unseen_metrics = result_metrics(test_result)
    reasons = _period_reasons(unseen_metrics, config)
    return GatePeriodResult(
        symbol=symbol,
        strategy=strategy_name,
        period=period_index,
        train_start=train[0].timestamp.isoformat(),
        train_end=train[-1].timestamp.isoformat(),
        unseen_start=unseen[0].timestamp.isoformat(),
        unseen_end=unseen[-1].timestamp.isoformat(),
        selected_parameters=strategy_params,
        selected_execution=execution_profile,
        train_metrics=selected["train_metrics"],
        unseen_metrics=unseen_metrics,
        cash_return=0.0,
        buy_and_hold_return=float(unseen_metrics["buy_and_hold_return"]),
        excess_vs_cash=float(unseen_metrics["net_return"]),
        excess_vs_buy_and_hold=float(unseen_metrics["excess_return"]),
        passed=not reasons,
        rejection_reasons=reasons,
    )


def _score_strategy(
    strategy_name: str,
    periods: list[GatePeriodResult],
    config: ResearchGateConfig,
) -> StrategyGateResult:
    reasons: list[str] = []
    if len(periods) < config.min_independent_periods:
        reasons.append(
            f"Only {len(periods)} independent unseen periods were available; "
            f"{config.min_independent_periods} are required."
        )

    returns = [float(period.unseen_metrics["net_return"]) for period in periods]
    positive_fraction = (
        sum(value > config.min_positive_unseen_return for value in returns) / len(returns)
        if returns
        else 0.0
    )
    beat_fraction = (
        sum(period.excess_vs_buy_and_hold > 0 for period in periods) / len(periods)
        if periods
        else 0.0
    )
    if config.require_all_unseen_positive and periods and positive_fraction < 1.0:
        reasons.append("At least one independent unseen period had a non-positive net return.")
    if beat_fraction < config.min_beat_buy_hold_fraction:
        reasons.append(
            "The strategy did not beat buy-and-hold in enough independent unseen periods."
        )

    failing_periods = [period for period in periods if not period.passed]
    if failing_periods:
        reasons.append(f"{len(failing_periods)} unseen periods failed risk, churn, or cost gates.")

    average_return = mean(returns) if returns else 0.0
    median_return = median(returns) if returns else 0.0
    average_excess = mean([period.excess_vs_buy_and_hold for period in periods]) if periods else 0.0
    worst_return = min(returns) if returns else 0.0
    worst_drawdown = max(
        (float(period.unseen_metrics["max_drawdown"]) for period in periods),
        default=0.0,
    )
    average_cost_drag = mean(
        [float(period.unseen_metrics.get("cost_drag_ratio", 0.0)) for period in periods]
    ) if periods else 0.0
    average_trade_rate = mean(
        [float(period.unseen_metrics.get("trades_per_100_bars", 0.0)) for period in periods]
    ) if periods else 0.0
    average_holding = mean(
        [float(period.unseen_metrics.get("average_holding_bars", 0.0)) for period in periods]
    ) if periods else 0.0

    passed = len(periods) >= config.min_independent_periods and not reasons
    return StrategyGateResult(
        strategy=strategy_name,
        passed=passed,
        reasons=reasons or ["All historical research gates passed."],
        independent_periods=len(periods),
        positive_unseen_fraction=positive_fraction,
        beat_buy_and_hold_fraction=beat_fraction,
        average_unseen_return=average_return,
        median_unseen_return=median_return,
        average_excess_vs_buy_and_hold=average_excess,
        worst_unseen_return=worst_return,
        worst_drawdown=worst_drawdown,
        average_cost_drag_ratio=average_cost_drag,
        average_trades_per_100_bars=average_trade_rate,
        average_holding_bars=average_holding,
        periods=periods,
    )


def evaluate_research_gate(
    folder: str | Path,
    market: Market = Market.CRYPTO,
    config: ResearchGateConfig | None = None,
) -> ResearchGateReport:
    config = config or ResearchGateConfig()
    histories = load_histories(folder)
    strategy_results: list[StrategyGateResult] = []

    for strategy_name in STRATEGIES:
        periods: list[GatePeriodResult] = []
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
        strategy_results.append(_score_strategy(strategy_name, periods, config))

    passed = [result for result in strategy_results if result.passed]
    champion = (
        max(
            passed,
            key=lambda result: (
                result.average_unseen_return,
                result.average_excess_vs_buy_and_hold,
                -result.worst_drawdown,
            ),
        ).strategy
        if passed
        else None
    )
    accepted = bool(passed)
    reasons = (
        [
            f"Historical gates passed for: {', '.join(result.strategy for result in passed)}.",
            f"Champion strategy for forward paper testing: {champion}.",
        ]
        if accepted
        else [
            "No strategy passed every required historical gate.",
            "Continuous forward paper trading remains blocked.",
        ]
    )
    return ResearchGateReport(
        schema_version=GATE_SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        market=market.value,
        dataset_fingerprint=dataset_fingerprint(histories),
        symbols=sorted(histories),
        config=asdict(config),
        strategies=strategy_results,
        accepted=accepted,
        eligible_for_continuous_paper=accepted,
        champion_strategy=champion,
        passed_strategies=[result.strategy for result in passed],
        reasons=reasons,
    )


def write_gate_report(path: str | Path, report: ResearchGateReport) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    temporary.replace(output)


def validate_forward_gate(
    path: str | Path,
    *,
    strategy_name: str,
    market: Market = Market.CRYPTO,
    max_age_days: int = 90,
) -> dict[str, Any]:
    if max_age_days < 1:
        raise ValueError("max_age_days must be positive")
    report_path = Path(path)
    if not report_path.exists():
        raise ValueError(f"Research gate report not found: {report_path}")
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid research gate report: {exc}") from exc

    if payload.get("schema_version") != GATE_SCHEMA_VERSION:
        raise ValueError("Unsupported research gate report schema")
    if payload.get("market") != market.value:
        raise ValueError(f"Gate report market must be {market.value}")
    if not payload.get("accepted") or not payload.get("eligible_for_continuous_paper"):
        raise ValueError("Historical research gates did not pass; continuous paper trading is blocked")
    if strategy_name not in payload.get("passed_strategies", []):
        raise ValueError(f"Strategy {strategy_name} did not pass the historical research gates")

    try:
        generated = datetime.fromisoformat(str(payload["generated_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Gate report generated_at is missing or invalid") from exc
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - generated.astimezone(timezone.utc)).total_seconds()
    if age_seconds > max_age_days * 86400:
        raise ValueError("Research gate report is stale; rerun the historical gates")

    return payload
