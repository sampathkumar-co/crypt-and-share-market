from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

from tradebot.backtest.combined_ablation import (
    COMBINED_FAMILY,
    CombinedAblationConfig,
    _evaluate_alpha_result,
    _evaluate_v05_reference,
    _screen_alpha_candidates,
    select_alpha_plateau,
)
from tradebot.backtest.meta_allocation import (
    _annualized_volatility,
    _close_returns,
    _pad_curve,
    correlation_filter,
    next_drawdown_multiplier,
)
from tradebot.backtest.metrics import max_drawdown, period_returns, sharpe_ratio, sortino_ratio
from tradebot.backtest.profit_quality_gate import _cash_metrics`r`nfrom tradebot.backtest.research_gate import (
    dataset_fingerprint,
    independent_train_test_windows,
    load_histories,
)
from tradebot.models import BacktestResult, Candle, Market


VALIDATION_SCHEMA_VERSION = "1.0"
REQUIRED_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT")


@dataclass(frozen=True)
class RelativeStrengthValidationConfig:
    history_bars: int = 1000
    discovery_holdout_bars: int = 365
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
    min_portfolio_periods: int = 7
    min_active_portfolio_periods: int = 4
    min_positive_period_fraction: float = 4 / 7
    min_beat_v05_fraction: float = 0.50
    min_beat_buy_hold_fraction: float = 0.50
    min_leave_one_out_positive_fraction: float = 0.80
    max_portfolio_drawdown: float = 0.05
    extra_cost_per_turnover: float = 0.001
    starting_cash: float = 100000.0

    def __post_init__(self) -> None:
        if self.history_bars != 1000 or self.discovery_holdout_bars != 365:
            raise ValueError("Validation requires the frozen 1000/365 history split")
        if self.train_size != 180 or self.test_size != 60:
            raise ValueError("Validation requires fixed 180/60 train-test windows")
        validation_bars = self.history_bars - self.discovery_holdout_bars
        expected_periods = (validation_bars - self.train_size) // self.test_size
        if expected_periods != self.min_portfolio_periods:
            raise ValueError("Configuration must preserve seven non-overlapping portfolio periods")
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
        for value, name in (
            (self.min_positive_period_fraction, "min_positive_period_fraction"),
            (self.min_beat_v05_fraction, "min_beat_v05_fraction"),
            (self.min_beat_buy_hold_fraction, "min_beat_buy_hold_fraction"),
            (self.min_leave_one_out_positive_fraction, "min_leave_one_out_positive_fraction"),
        ):
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between zero and one")
        if not 0 < self.max_portfolio_drawdown < 1:
            raise ValueError("max_portfolio_drawdown must be between zero and one")
        if self.extra_cost_per_turnover < 0:
            raise ValueError("extra_cost_per_turnover cannot be negative")
        if self.starting_cash <= 0:
            raise ValueError("starting_cash must be positive")


@dataclass
class ValidationAssetPeriod:
    symbol: str
    period: int
    train_start: str
    train_end: str
    unseen_start: str
    unseen_end: str
    eligible: bool
    eligibility_reasons: list[str]
    stable_candidate_count: int
    plateau_size: int
    consensus_strength: float
    annualized_training_volatility: float
    selected_parameters: dict[str, Any]
    selected_execution: dict[str, Any]
    v05_reference_strategy: str
    v05_metrics: dict[str, float | int]
    relative_strength_metrics: dict[str, float | int]
    buy_and_hold_return: float


@dataclass
class ValidationPortfolioPeriod:
    period: int
    unseen_start: str
    unseen_end: str
    selected_symbols: list[str]
    correlation_rejections: list[str]
    asset_weights: dict[str, float]
    cash_weight: float
    risk_multiplier: float
    portfolio_metrics: dict[str, float | int]
    v05_metrics: dict[str, float | int]
    equal_weight_buy_and_hold_return: float
    improvement_vs_v05: float
    excess_vs_buy_and_hold: float
    stressed_net_return: float


@dataclass
class RelativeStrengthValidationReport:
    schema_version: str
    generated_at: str
    market: str
    full_dataset_fingerprint: str
    validation_dataset_fingerprint: str
    symbols: list[str]
    config: dict[str, Any]
    discovery_start: str
    validation_end: str
    discovery_overlap_bars: int
    asset_periods: list[ValidationAssetPeriod]
    portfolio_periods: list[ValidationPortfolioPeriod]
    active_portfolio_periods: int
    average_portfolio_return: float
    median_portfolio_return: float
    compounded_portfolio_return: float
    positive_portfolio_fraction: float
    average_v05_return: float
    average_improvement_vs_v05: float
    beat_v05_fraction: float
    average_buy_and_hold_return: float
    average_excess_vs_buy_and_hold: float
    beat_buy_and_hold_fraction: float
    average_stressed_return: float
    worst_portfolio_drawdown: float
    leave_one_out_average_returns: dict[str, float]
    leave_one_out_positive_fraction: float
    accepted: bool
    eligible_for_forward_paper: bool
    reasons: list[str]
    paper_only: bool = True
    authorizes_real_trading: bool = False


@dataclass
class _AssetEvidence:
    public: ValidationAssetPeriod
    train: list[Candle]
    unseen: list[Candle]
    v05_curve: list[float]
    strategy_curve: list[float]


def _combined_config(config: RelativeStrengthValidationConfig) -> CombinedAblationConfig:
    return CombinedAblationConfig(
        train_size=config.train_size,
        test_size=config.test_size,
        max_candidates=config.max_candidates,
        screen_candidates=config.screen_candidates,
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
        min_deployed_periods=config.min_active_portfolio_periods,
        max_drawdown=config.max_portfolio_drawdown,
        starting_cash=config.starting_cash,
    )


def split_validation_histories(
    histories: dict[str, list[Candle]],
    config: RelativeStrengthValidationConfig,
) -> tuple[dict[str, list[Candle]], dict[str, list[Candle]]]:
    missing = sorted(set(REQUIRED_SYMBOLS) - set(histories))
    if missing:
        raise ValueError(f"Missing required validation histories: {', '.join(missing)}")
    full: dict[str, list[Candle]] = {}
    validation: dict[str, list[Candle]] = {}
    for symbol in REQUIRED_SYMBOLS:
        candles = histories[symbol]
        if len(candles) < config.history_bars:
            raise ValueError(
                f"{symbol} has {len(candles)} candles; {config.history_bars} required"
            )
        selected = candles[-config.history_bars :]
        full[symbol] = selected
        validation[symbol] = selected[: -config.discovery_holdout_bars]
        if len(validation[symbol]) != config.history_bars - config.discovery_holdout_bars:
            raise ValueError(f"Unexpected validation split length for {symbol}")
        if validation[symbol][-1].timestamp >= selected[-config.discovery_holdout_bars].timestamp:
            raise ValueError("Validation and discovery windows overlap")
    return full, validation


def compounded_return(returns: list[float]) -> float:
    value = 1.0
    for item in returns:
        value *= 1.0 + item
    return value - 1.0


def _cash_curve(length: int, cash: float) -> list[float]:
    return [cash] * max(1, length)


def _evaluate_asset_period(
    symbol: str,
    market: Market,
    period_index: int,
    train: list[Candle],
    unseen: list[Candle],
    histories: dict[str, list[Candle]],
    config: RelativeStrengthValidationConfig,
) -> _AssetEvidence:
    combined_config = _combined_config(config)
    stable, _ = _screen_alpha_candidates(
        symbol,
        market,
        train,
        histories,
        combined_config,
    )
    plateau = select_alpha_plateau(stable, combined_config)
    volatility = _annualized_volatility(train)
    reasons = list(plateau["reasons"])
    selected = stable[0] if stable else None
    if selected is None and not reasons:
        reasons.append("no_single_stable_relative_strength_candidate")

    reference_name, v05_metrics, v05_curve = _evaluate_v05_reference(
        symbol,
        market,
        train,
        unseen,
        combined_config,
    )
    if selected is None:
        strategy_metrics = _cash_metrics(unseen, config.starting_cash)
        strategy_curve = _cash_curve(len(v05_curve), config.starting_cash)
        selected_parameters: dict[str, Any] = {}
        selected_execution: dict[str, Any] = {}
    else:
        result = _evaluate_alpha_result(
            symbol,
            market,
            train,
            unseen,
            selected,
            histories,
            config.starting_cash,
        )
        strategy_metrics = _result_metrics(result)
        strategy_curve = result.equity_curve
        selected_parameters = selected["strategy_parameters"]
        selected_execution = selected["execution_parameters"]

    public = ValidationAssetPeriod(
        symbol=symbol,
        period=period_index,
        train_start=train[0].timestamp.isoformat(),
        train_end=train[-1].timestamp.isoformat(),
        unseen_start=unseen[0].timestamp.isoformat(),
        unseen_end=unseen[-1].timestamp.isoformat(),
        eligible=not reasons,
        eligibility_reasons=reasons,
        stable_candidate_count=len(stable),
        plateau_size=len(plateau["plateau"]),
        consensus_strength=float(plateau["consensus_strength"]),
        annualized_training_volatility=volatility,
        selected_parameters=selected_parameters,
        selected_execution=selected_execution,
        v05_reference_strategy=reference_name,
        v05_metrics=v05_metrics,
        relative_strength_metrics=strategy_metrics,
        buy_and_hold_return=float(strategy_metrics["buy_and_hold_return"]),
    )
    return _AssetEvidence(
        public=public,
        train=train,
        unseen=unseen,
        v05_curve=v05_curve,
        strategy_curve=strategy_curve,
    )


def _result_metrics(result: BacktestResult) -> dict[str, float | int]:
    return {
        "net_return": result.net_return,
        "gross_return": result.gross_return,
        "buy_and_hold_return": result.buy_and_hold_return,
        "max_drawdown": result.max_drawdown,
        "trades": len(result.trades),
        "turnover": result.turnover,
        "cost_drag_ratio": result.cost_drag_ratio,
        "total_fees": result.total_fees,
        "total_tax": result.total_tax,
        "total_slippage": result.total_slippage,
        "ending_cash": result.ending_cash,
        "sharpe_ratio": result.sharpe_ratio,
        "sortino_ratio": result.sortino_ratio,
        "average_holding_bars": result.average_holding_bars,
    }


def _portfolio_metrics(
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
    turnover = sum(
        weights.get(item.public.symbol, 0.0)
        * float(getattr(item.public, metrics_name).get("turnover", 0.0))
        for item in evidence
    )
    return {
        "net_return": net_return,
        "ending_cash": ending_cash,
        "max_drawdown": drawdown,
        "trades": sum(
            int(getattr(item.public, metrics_name).get("trades", 0))
            for item in evidence
            if item.public.symbol in weights
        ),
        "turnover": turnover,
        "sharpe_ratio": sharpe_ratio(returns, 365),
        "sortino_ratio": sortino_ratio(returns, 365),
        "calmar_ratio": net_return / drawdown if drawdown > 0 else 0.0,
        "exposure": sum(weights.values()),
    }


def _period_weights(
    current: list[_AssetEvidence],
    risk_multiplier: float,
    config: RelativeStrengthValidationConfig,
) -> tuple[dict[str, float], list[str]]:
    selected, rejected = correlation_filter(
        [
            (
                item.public.symbol,
                item.public.consensus_strength,
                _close_returns(item.train),
            )
            for item in current
            if item.public.eligible
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
            symbol: min(config.max_asset_exposure, max_total * score / raw_total)
            for symbol, score in raw_scores.items()
        }
        allocated = sum(weights.values())
        if allocated > max_total:
            scale = max_total / allocated
            weights = {symbol: weight * scale for symbol, weight in weights.items()}
    return weights, rejected


def _build_portfolio_periods(
    evidence: list[_AssetEvidence],
    config: RelativeStrengthValidationConfig,
) -> list[ValidationPortfolioPeriod]:
    grouped: dict[int, list[_AssetEvidence]] = {}
    for item in evidence:
        grouped.setdefault(item.public.period, []).append(item)
    combined_config = _combined_config(config)
    output: list[ValidationPortfolioPeriod] = []
    risk_multiplier = 1.0
    previous_drawdown = 0.0
    for period_index in sorted(grouped):
        current = grouped[period_index]
        if output:
            risk_multiplier = next_drawdown_multiplier(
                risk_multiplier,
                previous_drawdown,
                combined_config,
            )
        weights, rejected = _period_weights(current, risk_multiplier, config)
        portfolio_metrics = _portfolio_metrics(
            current,
            weights,
            "strategy_curve",
            "relative_strength_metrics",
            config.starting_cash,
        )
        v05_metrics = _portfolio_metrics(
            current,
            weights,
            "v05_curve",
            "v05_metrics",
            config.starting_cash,
        )
        benchmark = mean(item.public.buy_and_hold_return for item in current)
        stressed = (
            float(portfolio_metrics["net_return"])
            - config.extra_cost_per_turnover * float(portfolio_metrics["turnover"])
        )
        previous_drawdown = float(portfolio_metrics["max_drawdown"])
        first = current[0].public
        output.append(
            ValidationPortfolioPeriod(
                period=period_index,
                unseen_start=first.unseen_start,
                unseen_end=first.unseen_end,
                selected_symbols=sorted(weights),
                correlation_rejections=sorted(rejected),
                asset_weights=weights,
                cash_weight=max(0.0, 1.0 - sum(weights.values())),
                risk_multiplier=risk_multiplier,
                portfolio_metrics=portfolio_metrics,
                v05_metrics=v05_metrics,
                equal_weight_buy_and_hold_return=benchmark,
                improvement_vs_v05=(
                    float(portfolio_metrics["net_return"])
                    - float(v05_metrics["net_return"])
                ),
                excess_vs_buy_and_hold=(
                    float(portfolio_metrics["net_return"]) - benchmark
                ),
                stressed_net_return=stressed,
            )
        )
    return output


def _leave_one_out_returns(
    evidence: list[_AssetEvidence],
    portfolio_periods: list[ValidationPortfolioPeriod],
    config: RelativeStrengthValidationConfig,
) -> dict[str, float]:
    grouped: dict[int, list[_AssetEvidence]] = {}
    for item in evidence:
        grouped.setdefault(item.public.period, []).append(item)
    averages: dict[str, float] = {}
    for omitted in REQUIRED_SYMBOLS:
        returns: list[float] = []
        for portfolio in portfolio_periods:
            current = grouped[portfolio.period]
            weights = {
                symbol: weight
                for symbol, weight in portfolio.asset_weights.items()
                if symbol != omitted
            }
            metrics = _portfolio_metrics(
                current,
                weights,
                "strategy_curve",
                "relative_strength_metrics",
                config.starting_cash,
            )
            returns.append(float(metrics["net_return"]))
        averages[omitted] = mean(returns) if returns else 0.0
    return averages


def evaluate_relative_strength_validation(
    folder: str | Path,
    market: Market = Market.CRYPTO,
    config: RelativeStrengthValidationConfig | None = None,
) -> RelativeStrengthValidationReport:
    config = config or RelativeStrengthValidationConfig()
    loaded = load_histories(folder)
    full, validation = split_validation_histories(loaded, config)
    evidence: list[_AssetEvidence] = []
    for symbol in REQUIRED_SYMBOLS:
        windows = independent_train_test_windows(
            validation[symbol],
            config.train_size,
            config.test_size,
        )
        if len(windows) != config.min_portfolio_periods:
            raise ValueError(
                f"{symbol} produced {len(windows)} validation periods; "
                f"{config.min_portfolio_periods} required"
            )
        for period_index, (train, unseen) in enumerate(windows, start=1):
            evidence.append(
                _evaluate_asset_period(
                    symbol,
                    market,
                    period_index,
                    train,
                    unseen,
                    validation,
                    config,
                )
            )
    portfolio_periods = _build_portfolio_periods(evidence, config)
    returns = [float(item.portfolio_metrics["net_return"]) for item in portfolio_periods]
    v05_returns = [float(item.v05_metrics["net_return"]) for item in portfolio_periods]
    buy_hold_returns = [item.equal_weight_buy_and_hold_return for item in portfolio_periods]
    stressed_returns = [item.stressed_net_return for item in portfolio_periods]
    improvements = [item.improvement_vs_v05 for item in portfolio_periods]
    excess_buy_hold = [item.excess_vs_buy_and_hold for item in portfolio_periods]
    active_periods = sum(bool(item.selected_symbols) for item in portfolio_periods)
    positive_fraction = sum(item > 0 for item in returns) / len(returns)
    beat_v05_fraction = sum(item > 0 for item in improvements) / len(improvements)
    beat_buy_hold_fraction = sum(item > 0 for item in excess_buy_hold) / len(excess_buy_hold)
    leave_one_out = _leave_one_out_returns(evidence, portfolio_periods, config)
    leave_one_out_positive = (
        sum(value > 0 for value in leave_one_out.values()) / len(leave_one_out)
    )

    average_return = mean(returns)
    median_return = median(returns)
    compounded = compounded_return(returns)
    average_v05 = mean(v05_returns)
    average_improvement = mean(improvements)
    average_buy_hold = mean(buy_hold_returns)
    average_excess_buy_hold = mean(excess_buy_hold)
    average_stressed = mean(stressed_returns)
    worst_drawdown = max(
        (float(item.portfolio_metrics["max_drawdown"]) for item in portfolio_periods),
        default=0.0,
    )

    reasons: list[str] = []
    if len(portfolio_periods) < config.min_portfolio_periods:
        reasons.append("too_few_non_overlapping_portfolio_periods")
    if active_periods < config.min_active_portfolio_periods:
        reasons.append("too_few_active_portfolio_periods")
    if average_return <= 0:
        reasons.append("average_validation_portfolio_return_not_positive")
    if median_return <= 0:
        reasons.append("median_validation_portfolio_return_not_positive")
    if compounded <= 0:
        reasons.append("compounded_validation_return_not_positive")
    if positive_fraction < config.min_positive_period_fraction:
        reasons.append("too_few_positive_validation_periods")
    if average_improvement <= 0:
        reasons.append("no_average_improvement_over_frozen_v05")
    if beat_v05_fraction < config.min_beat_v05_fraction:
        reasons.append("did_not_beat_frozen_v05_often_enough")
    if average_excess_buy_hold <= 0:
        reasons.append("no_average_excess_over_equal_weight_buy_and_hold")
    if beat_buy_hold_fraction < config.min_beat_buy_hold_fraction:
        reasons.append("did_not_beat_buy_and_hold_often_enough")
    if average_stressed <= 0:
        reasons.append("return_not_positive_after_extra_cost_stress")
    if worst_drawdown > config.max_portfolio_drawdown:
        reasons.append("portfolio_drawdown_exceeded_validation_limit")
    if leave_one_out_positive < config.min_leave_one_out_positive_fraction:
        reasons.append("portfolio_edge_depends_on_too_few_assets")

    discovery_start = min(
        full[symbol][-config.discovery_holdout_bars].timestamp
        for symbol in REQUIRED_SYMBOLS
    )
    validation_end = max(validation[symbol][-1].timestamp for symbol in REQUIRED_SYMBOLS)
    return RelativeStrengthValidationReport(
        schema_version=VALIDATION_SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        market=market.value,
        full_dataset_fingerprint=dataset_fingerprint(full),
        validation_dataset_fingerprint=dataset_fingerprint(validation),
        symbols=list(REQUIRED_SYMBOLS),
        config=asdict(config),
        discovery_start=discovery_start.isoformat(),
        validation_end=validation_end.isoformat(),
        discovery_overlap_bars=0,
        asset_periods=[item.public for item in evidence],
        portfolio_periods=portfolio_periods,
        active_portfolio_periods=active_periods,
        average_portfolio_return=average_return,
        median_portfolio_return=median_return,
        compounded_portfolio_return=compounded,
        positive_portfolio_fraction=positive_fraction,
        average_v05_return=average_v05,
        average_improvement_vs_v05=average_improvement,
        beat_v05_fraction=beat_v05_fraction,
        average_buy_and_hold_return=average_buy_hold,
        average_excess_vs_buy_and_hold=average_excess_buy_hold,
        beat_buy_and_hold_fraction=beat_buy_hold_fraction,
        average_stressed_return=average_stressed,
        worst_portfolio_drawdown=worst_drawdown,
        leave_one_out_average_returns=leave_one_out,
        leave_one_out_positive_fraction=leave_one_out_positive,
        accepted=not reasons,
        eligible_for_forward_paper=not reasons,
        reasons=(
            reasons
            or [
                "The frozen relative-strength portfolio passed non-overlapping historical validation."
            ]
        ),
    )


def write_validation_report(
    path: str | Path,
    report: RelativeStrengthValidationReport,
) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    temporary.replace(output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the frozen single-candidate relative-strength portfolio "
            "on the 635 older bars preceding the 365-bar discovery window."
        )
    )
    parser.add_argument("--folder", required=True)
    parser.add_argument(
        "--market",
        choices=[item.value for item in Market],
        default=Market.CRYPTO.value,
    )
    parser.add_argument("--json-out", default="reports/relative_strength_validation.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = evaluate_relative_strength_validation(
        args.folder,
        market=Market(args.market),
    )
    write_validation_report(args.json_out, report)
    print(json.dumps(asdict(report), indent=2))
    return 0 if report.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
