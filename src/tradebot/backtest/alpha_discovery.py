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
from tradebot.backtest.profit_quality_gate import (
    ProfitQualityConfig,
    _cash_metrics,
    _evaluate_unseen as evaluate_v05_unseen,
    _select_candidates as select_v05_candidates,
)
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
from tradebot.backtest.walk_forward import parameter_grid, result_metrics
from tradebot.models import Candle, Market
from tradebot.strategies.compression_breakout import CompressionBreakoutRetestStrategy
from tradebot.strategies.multi_timeframe_trend import MultiTimeframeTrendStrategy
from tradebot.strategies.relative_strength import CrossAssetRelativeStrengthStrategy


TRACK_A_SCHEMA_VERSION = "1.0"
TRACK_A_FAMILIES = (
    "multi_timeframe_trend",
    "compression_breakout_retest",
    "cross_asset_relative_strength",
)
V05_STRATEGIES = ("momentum", "breakout", "mean_reversion")
REQUIRED_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT")
HISTORY_BARS = 365


ALPHA_PARAMETER_GRIDS: dict[str, dict[str, list[Any]]] = {
    "multi_timeframe_trend": {
        "fast_window": [5, 8],
        "medium_window": [20, 30],
        "slow_window": [60, 90],
        "pullback_tolerance": [0.01, 0.02],
        "volume_multiplier": [0.90, 1.10],
    },
    "compression_breakout_retest": {
        "compression_window": [15, 20],
        "max_atr_ratio": [0.65, 0.80],
        "breakout_buffer": [0.0, 0.003],
        "retest_bars": [2, 4],
        "volume_multiplier": [0.90, 1.10],
    },
    "cross_asset_relative_strength": {
        "lookback": [20, 40, 60],
        "short_lookback": [5, 10],
        "top_n": [1, 2],
        "min_return": [0.0, 0.03],
    },
}


@dataclass(frozen=True)
class AlphaDiscoveryConfig:
    train_size: int = 180
    test_size: int = 60
    max_candidates_per_family: int = 120
    stability_screen_candidates: int = 24
    min_deployed_periods: int = 6
    min_positive_deployed_fraction: float = 0.50
    min_profitable_assets: int = 2
    max_drawdown: float = 0.20
    starting_cash: float = 100000.0
    stability_policy: TemporalStabilityPolicy = field(default_factory=TemporalStabilityPolicy)

    def __post_init__(self) -> None:
        if self.train_size != 180 or self.test_size != 60:
            raise ValueError("Track A requires fixed 180-bar training and 60-bar unseen windows")
        if self.max_candidates_per_family < 1:
            raise ValueError("max_candidates_per_family must be positive")
        if not 1 <= self.stability_screen_candidates <= self.max_candidates_per_family:
            raise ValueError("stability_screen_candidates must be within the candidate budget")
        if self.min_deployed_periods < 1:
            raise ValueError("min_deployed_periods must be positive")
        if not 0 <= self.min_positive_deployed_fraction <= 1:
            raise ValueError("min_positive_deployed_fraction must be between 0 and 1")
        if self.min_profitable_assets < 2:
            raise ValueError("Track A requires positive results on at least two assets")
        if not 0 < self.max_drawdown < 1:
            raise ValueError("max_drawdown must be between 0 and 1")
        if self.starting_cash <= 0:
            raise ValueError("starting_cash must be positive")


@dataclass
class AlphaDiscoveryPeriod:
    symbol: str
    family: str
    period: int
    train_start: str
    train_end: str
    unseen_start: str
    unseen_end: str
    abstained: bool
    selection_reasons: list[str]
    selected_parameters: dict[str, Any]
    selected_execution: dict[str, Any]
    training_stability: dict[str, float | int | bool]
    metrics: dict[str, float | int]
    cash_return: float
    buy_and_hold_return: float
    existing_strategy_metrics: dict[str, dict[str, float | int]]
    v05_reference_strategy: str
    v05_reference_metrics: dict[str, float | int]
    improvement_over_v05_reference: float


@dataclass
class AlphaFamilyResult:
    family: str
    promising: bool
    reasons: list[str]
    periods: list[AlphaDiscoveryPeriod]
    deployed_periods: int
    abstained_periods: int
    average_unseen_net_return: float
    positive_deployed_fraction: float
    average_cash_return: float
    average_buy_and_hold_return: float
    average_excess_vs_buy_and_hold: float
    average_v05_reference_return: float
    average_improvement_over_v05: float
    worst_drawdown: float
    deployed_assets: list[str]
    profitable_assets: list[str]
    asset_average_returns: dict[str, float]


@dataclass
class AlphaDiscoveryReport:
    schema_version: str
    generated_at: str
    market: str
    dataset_fingerprint: str
    history_bars: int
    symbols: list[str]
    config: dict[str, Any]
    families: list[AlphaFamilyResult]
    existing_strategy_average_returns: dict[str, float]
    promising_families: list[str]
    accepted: bool
    reasons: list[str]
    experiment_count: int
    paper_only: bool = True
    long_or_cash_only: bool = True
    authorizes_real_trading: bool = False


def load_fixed_histories(folder: str | Path) -> dict[str, list[Candle]]:
    loaded = load_histories(folder)
    missing = sorted(set(REQUIRED_SYMBOLS) - set(loaded))
    if missing:
        raise ValueError(f"Missing required Track A histories: {', '.join(missing)}")
    histories: dict[str, list[Candle]] = {}
    for symbol in REQUIRED_SYMBOLS:
        candles = loaded[symbol]
        if len(candles) < HISTORY_BARS:
            raise ValueError(
                f"{symbol} has {len(candles)} candles; Track A requires {HISTORY_BARS} daily candles"
            )
        histories[symbol] = candles[-HISTORY_BARS:]
    return histories


def _slice_histories(
    histories: dict[str, list[Candle]],
    start: datetime,
    end: datetime,
) -> dict[str, list[Candle]]:
    return {
        symbol: [candle for candle in candles if start <= candle.timestamp <= end]
        for symbol, candles in histories.items()
    }


def _strategy_warmup(family: str, params: dict[str, Any]) -> int:
    if family == "multi_timeframe_trend":
        return max(
            int(params.get("slow_window", 63)) + 10,
            int(params.get("medium_window", 21)) + 2,
            12,
        )
    if family == "compression_breakout_retest":
        return (
            int(params.get("compression_window", 20))
            + int(params.get("retest_bars", 3))
            + 20
            + 2
        )
    if family == "cross_asset_relative_strength":
        return int(params.get("lookback", 40)) + 1
    raise ValueError(f"Unknown Track A family: {family}")


def build_alpha_strategy(
    family: str,
    params: dict[str, Any],
    *,
    symbol: str,
    peer_histories: dict[str, list[Candle]],
):
    if family == "multi_timeframe_trend":
        return MultiTimeframeTrendStrategy(
            slope_window=10,
            min_slow_slope=0.005,
            breakout_lookback=10,
            continuation_buffer=0.002,
            max_extension=0.08,
            **params,
        )
    if family == "compression_breakout_retest":
        return CompressionBreakoutRetestStrategy(
            atr_short_window=5,
            atr_long_window=20,
            max_range_pct=0.12,
            retest_tolerance=0.015,
            allow_continuation=True,
            max_extension=0.05,
            **params,
        )
    if family == "cross_asset_relative_strength":
        top_n = int(params.get("top_n", 2))
        return CrossAssetRelativeStrengthStrategy(
            symbol,
            peer_histories,
            exit_rank=max(3, top_n + 1),
            min_breadth=0.40,
            volatility_penalty=0.25,
            **params,
        )
    raise ValueError(f"Unknown Track A family: {family}")


def _candidate_grid(
    family: str,
    config: AlphaDiscoveryConfig,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    params = parameter_grid(ALPHA_PARAMETER_GRIDS[family])
    return list(product(params, EXECUTION_PROFILES))[: config.max_candidates_per_family]


def _run_alpha_candidate(
    symbol: str,
    market: Market,
    candles: list[Candle],
    family: str,
    params: dict[str, Any],
    execution: dict[str, Any],
    peer_histories: dict[str, list[Candle]],
    starting_cash: float,
    *,
    trade_start_index: int = 0,
) -> dict[str, float | int]:
    warmup = _strategy_warmup(family, params)
    strategy = build_alpha_strategy(
        family,
        params,
        symbol=symbol,
        peer_histories=peer_histories,
    )
    result = PaperTrader(
        market,
        strategy,
        starting_cash=starting_cash,
        config=_execution_config(execution, warmup_bars=warmup),
    ).run(symbol, candles, trade_start_index=trade_start_index)
    return result_metrics(result)


def _training_fold_metrics(
    symbol: str,
    market: Market,
    train: list[Candle],
    family: str,
    params: dict[str, Any],
    execution: dict[str, Any],
    histories: dict[str, list[Candle]],
    config: AlphaDiscoveryConfig,
) -> list[dict[str, float | int]]:
    warmup = max(10, _strategy_warmup(family, params))
    metrics: list[dict[str, float | int]] = []
    for fold_start, fold_end in temporal_fold_ranges(len(train), config.stability_policy):
        history_start = max(0, fold_start - warmup)
        target_history = train[history_start:fold_end]
        if not target_history:
            continue
        peer_histories = _slice_histories(
            histories,
            target_history[0].timestamp,
            target_history[-1].timestamp,
        )
        metrics.append(
            _run_alpha_candidate(
                symbol,
                market,
                target_history,
                family,
                params,
                execution,
                peer_histories,
                config.starting_cash,
                trade_start_index=max(0, fold_start - history_start),
            )
        )
    return metrics


def _select_alpha_candidate(
    symbol: str,
    market: Market,
    train: list[Candle],
    family: str,
    histories: dict[str, list[Candle]],
    config: AlphaDiscoveryConfig,
    *,
    period: int,
) -> dict[str, Any]:
    peer_train = _slice_histories(histories, train[0].timestamp, train[-1].timestamp)
    score_config = ResearchGateConfig(max_candidates_per_strategy=config.max_candidates_per_family)
    candidates: list[dict[str, Any]] = []
    for params, execution in _candidate_grid(family, config):
        metrics = _run_alpha_candidate(
            symbol,
            market,
            train,
            family,
            params,
            execution,
            peer_train,
            config.starting_cash,
        )
        candidates.append(
            {
                "strategy_parameters": params,
                "execution_parameters": execution,
                "train_metrics": metrics,
                "base_score": _candidate_score(metrics, score_config),
            }
        )
    if not candidates:
        raise ValueError(f"No Track A candidates generated for {family}")

    candidates.sort(key=lambda item: float(item["base_score"]), reverse=True)
    stable_candidates: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    best_screened: dict[str, Any] | None = None
    for index, candidate in enumerate(candidates):
        diagnostic = {
            "family": family,
            "symbol": symbol,
            "period": period,
            "rank_on_full_training": index + 1,
            "strategy_parameters": candidate["strategy_parameters"],
            "execution_parameters": candidate["execution_parameters"],
            "train_metrics": candidate["train_metrics"],
            "base_score": candidate["base_score"],
            "screened_for_temporal_stability": index < config.stability_screen_candidates,
        }
        if index < config.stability_screen_candidates:
            folds = _training_fold_metrics(
                symbol,
                market,
                train,
                family,
                candidate["strategy_parameters"],
                candidate["execution_parameters"],
                histories,
                config,
            )
            summary = summarize_fold_metrics(folds)
            reasons = stability_reasons(candidate["train_metrics"], summary, config.stability_policy)
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
            diagnostic.update(
                {
                    "fold_metrics": folds,
                    "training_stability": summary,
                    "selection_reasons": reasons,
                    "stability_score": enriched["stability_score"],
                }
            )
            if best_screened is None or float(enriched["stability_score"]) > float(best_screened["stability_score"]):
                best_screened = enriched
            if not reasons:
                stable_candidates.append(enriched)
        diagnostics.append(diagnostic)

    stable_candidates.sort(key=lambda item: float(item["stability_score"]), reverse=True)
    if best_screened is None:
        raise ValueError("Temporal stability screen did not evaluate any candidates")
    return {
        "selected": stable_candidates[0] if stable_candidates else None,
        "selection_reasons": list(best_screened.get("selection_reasons", [])),
        "best_screened": best_screened,
        "diagnostics": diagnostics,
        "candidate_count": len(candidates),
    }


def _evaluate_alpha_unseen(
    symbol: str,
    market: Market,
    train: list[Candle],
    unseen: list[Candle],
    family: str,
    candidate: dict[str, Any],
    histories: dict[str, list[Candle]],
    config: AlphaDiscoveryConfig,
) -> dict[str, float | int]:
    params = candidate["strategy_parameters"]
    execution = candidate["execution_parameters"]
    warmup_count = min(len(train), max(30, _strategy_warmup(family, params)))
    evaluation = [*train[-warmup_count:], *unseen]
    peer_histories = _slice_histories(
        histories,
        evaluation[0].timestamp,
        evaluation[-1].timestamp,
    )
    return _run_alpha_candidate(
        symbol,
        market,
        evaluation,
        family,
        params,
        execution,
        peer_histories,
        config.starting_cash,
        trade_start_index=warmup_count,
    )


def _evaluate_existing_strategies(
    symbol: str,
    market: Market,
    train: list[Candle],
    unseen: list[Candle],
    config: AlphaDiscoveryConfig,
) -> dict[str, Any]:
    v05_config = ProfitQualityConfig(
        train_size=config.train_size,
        test_size=config.test_size,
        max_candidates_per_strategy=120,
        stability_screen_candidates=24,
        starting_cash=config.starting_cash,
    )
    metrics_by_strategy: dict[str, dict[str, float | int]] = {}
    stable_candidates: list[tuple[str, float, dict[str, Any]]] = []
    for strategy_name in V05_STRATEGIES:
        selection = select_v05_candidates(symbol, market, train, strategy_name, v05_config)
        stable = selection["stable"]
        if stable is None:
            metrics_by_strategy[strategy_name] = _cash_metrics(unseen, config.starting_cash)
            continue
        metrics_by_strategy[strategy_name] = evaluate_v05_unseen(
            symbol,
            market,
            train,
            unseen,
            strategy_name,
            stable,
            v05_config,
        )
        stable_candidates.append((strategy_name, float(stable["stability_score"]), stable))

    if stable_candidates:
        reference_strategy = max(stable_candidates, key=lambda item: (item[1], item[0]))[0]
        reference_metrics = metrics_by_strategy[reference_strategy]
    else:
        reference_strategy = "cash"
        reference_metrics = _cash_metrics(unseen, config.starting_cash)
    return {
        "metrics_by_strategy": metrics_by_strategy,
        "reference_strategy": reference_strategy,
        "reference_metrics": reference_metrics,
    }


def _summarize_family(
    family: str,
    periods: list[AlphaDiscoveryPeriod],
    config: AlphaDiscoveryConfig,
) -> AlphaFamilyResult:
    deployed = [period for period in periods if not period.abstained]
    returns = [float(period.metrics["net_return"]) for period in periods]
    positive_fraction = (
        sum(float(period.metrics["net_return"]) > 0 for period in deployed) / len(deployed)
        if deployed
        else 0.0
    )
    average_return = mean(returns) if returns else 0.0
    average_buy_hold = mean([period.buy_and_hold_return for period in periods]) if periods else 0.0
    average_reference = mean(
        [float(period.v05_reference_metrics["net_return"]) for period in periods]
    ) if periods else 0.0
    average_improvement = mean(
        [period.improvement_over_v05_reference for period in periods]
    ) if periods else 0.0
    worst_drawdown = max(
        (float(period.metrics["max_drawdown"]) for period in periods),
        default=0.0,
    )

    asset_average_returns: dict[str, float] = {}
    for symbol in REQUIRED_SYMBOLS:
        asset_periods = [
            float(period.metrics["net_return"])
            for period in periods
            if period.symbol == symbol
        ]
        asset_average_returns[symbol] = mean(asset_periods) if asset_periods else 0.0
    deployed_assets = sorted({period.symbol for period in deployed})
    profitable_assets = sorted(
        symbol for symbol, value in asset_average_returns.items() if value > 0
    )

    reasons: list[str] = []
    if average_return <= 0:
        reasons.append("average_unseen_net_return_not_positive")
    if positive_fraction < config.min_positive_deployed_fraction:
        reasons.append("fewer_than_half_of_deployed_unseen_periods_profitable")
    if len(deployed) < config.min_deployed_periods:
        reasons.append("fewer_than_six_independent_unseen_deployments")
    if average_improvement <= 0:
        reasons.append("did_not_beat_training_selected_v05_reference_on_average")
    if len(profitable_assets) < config.min_profitable_assets:
        reasons.append("positive_results_dependent_on_fewer_than_two_assets")
    if worst_drawdown > config.max_drawdown:
        reasons.append("unseen_drawdown_not_controlled")

    return AlphaFamilyResult(
        family=family,
        promising=not reasons,
        reasons=reasons or ["Track A promising-candidate criteria passed."],
        periods=periods,
        deployed_periods=len(deployed),
        abstained_periods=len(periods) - len(deployed),
        average_unseen_net_return=average_return,
        positive_deployed_fraction=positive_fraction,
        average_cash_return=0.0,
        average_buy_and_hold_return=average_buy_hold,
        average_excess_vs_buy_and_hold=average_return - average_buy_hold,
        average_v05_reference_return=average_reference,
        average_improvement_over_v05=average_improvement,
        worst_drawdown=worst_drawdown,
        deployed_assets=deployed_assets,
        profitable_assets=profitable_assets,
        asset_average_returns=asset_average_returns,
    )


def evaluate_alpha_discovery(
    folder: str | Path,
    market: Market = Market.CRYPTO,
    config: AlphaDiscoveryConfig | None = None,
) -> tuple[AlphaDiscoveryReport, list[dict[str, Any]]]:
    config = config or AlphaDiscoveryConfig()
    histories = load_fixed_histories(folder)
    period_specs: list[tuple[str, int, list[Candle], list[Candle]]] = []
    for symbol in REQUIRED_SYMBOLS:
        windows = independent_train_test_windows(
            histories[symbol],
            config.train_size,
            config.test_size,
        )
        for period_index, (train, unseen) in enumerate(windows, start=1):
            period_specs.append((symbol, period_index, train, unseen))

    baseline_cache: dict[tuple[str, int], dict[str, Any]] = {}
    for symbol, period_index, train, unseen in period_specs:
        baseline_cache[(symbol, period_index)] = _evaluate_existing_strategies(
            symbol,
            market,
            train,
            unseen,
            config,
        )

    family_results: list[AlphaFamilyResult] = []
    diagnostics: list[dict[str, Any]] = []
    for family in TRACK_A_FAMILIES:
        periods: list[AlphaDiscoveryPeriod] = []
        for symbol, period_index, train, unseen in period_specs:
            selection = _select_alpha_candidate(
                symbol,
                market,
                train,
                family,
                histories,
                config,
                period=period_index,
            )
            diagnostics.extend(selection["diagnostics"])
            selected = selection["selected"]
            if selected is None:
                metrics = _cash_metrics(unseen, config.starting_cash)
                params: dict[str, Any] = {}
                execution: dict[str, Any] = {}
                training_stability = selection["best_screened"].get("training_stability", {})
            else:
                metrics = _evaluate_alpha_unseen(
                    symbol,
                    market,
                    train,
                    unseen,
                    family,
                    selected,
                    histories,
                    config,
                )
                params = selected["strategy_parameters"]
                execution = selected["execution_parameters"]
                training_stability = selected["training_stability"]

            baseline = baseline_cache[(symbol, period_index)]
            reference_metrics = baseline["reference_metrics"]
            periods.append(
                AlphaDiscoveryPeriod(
                    symbol=symbol,
                    family=family,
                    period=period_index,
                    train_start=train[0].timestamp.isoformat(),
                    train_end=train[-1].timestamp.isoformat(),
                    unseen_start=unseen[0].timestamp.isoformat(),
                    unseen_end=unseen[-1].timestamp.isoformat(),
                    abstained=selected is None,
                    selection_reasons=selection["selection_reasons"] if selected is None else [],
                    selected_parameters=params,
                    selected_execution=execution,
                    training_stability=training_stability,
                    metrics=metrics,
                    cash_return=0.0,
                    buy_and_hold_return=float(metrics["buy_and_hold_return"]),
                    existing_strategy_metrics=baseline["metrics_by_strategy"],
                    v05_reference_strategy=baseline["reference_strategy"],
                    v05_reference_metrics=reference_metrics,
                    improvement_over_v05_reference=(
                        float(metrics["net_return"])
                        - float(reference_metrics["net_return"])
                    ),
                )
            )
        family_results.append(_summarize_family(family, periods, config))

    existing_average_returns = {
        strategy_name: mean(
            [
                float(baseline["metrics_by_strategy"][strategy_name]["net_return"])
                for baseline in baseline_cache.values()
            ]
        )
        for strategy_name in V05_STRATEGIES
    }
    promising = [item.family for item in family_results if item.promising]
    report = AlphaDiscoveryReport(
        schema_version=TRACK_A_SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        market=market.value,
        dataset_fingerprint=dataset_fingerprint(histories),
        history_bars=HISTORY_BARS,
        symbols=list(REQUIRED_SYMBOLS),
        config=asdict(config),
        families=family_results,
        existing_strategy_average_returns=existing_average_returns,
        promising_families=promising,
        accepted=bool(promising),
        reasons=(
            [f"Promising Track A families: {', '.join(promising)}."]
            if promising
            else [
                "No new alpha family passed every Track A promising-candidate criterion.",
                "All failed experiments and unseen results are preserved in the report artifacts.",
            ]
        ),
        experiment_count=len(diagnostics),
    )
    return report, diagnostics


def write_json(path: str | Path, payload: Any) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    if hasattr(payload, "__dataclass_fields__"):
        content = asdict(payload)
    else:
        content = payload
    temporary.write_text(json.dumps(content, indent=2), encoding="utf-8")
    temporary.replace(output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Track A discovery of new paper-only alpha families without unseen optimisation."
    )
    parser.add_argument("--folder", required=True)
    parser.add_argument("--market", choices=[item.value for item in Market], default=Market.CRYPTO.value)
    parser.add_argument("--train-size", type=int, default=180)
    parser.add_argument("--test-size", type=int, default=60)
    parser.add_argument("--max-candidates", type=int, default=120)
    parser.add_argument("--screen-candidates", type=int, default=24)
    parser.add_argument("--json-out", default="reports/alpha-v06/alpha_discovery.json")
    parser.add_argument("--diagnostics-out", default="reports/alpha-v06/alpha_candidate_diagnostics.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = AlphaDiscoveryConfig(
        train_size=args.train_size,
        test_size=args.test_size,
        max_candidates_per_family=args.max_candidates,
        stability_screen_candidates=args.screen_candidates,
    )
    report, diagnostics = evaluate_alpha_discovery(
        args.folder,
        market=Market(args.market),
        config=config,
    )
    write_json(args.json_out, report)
    write_json(args.diagnostics_out, diagnostics)
    print(json.dumps(asdict(report), indent=2))
    return 0 if report.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
