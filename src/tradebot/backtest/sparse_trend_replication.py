from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from tradebot.backtest.cross_sectional_trend import (
    REQUIRED_SYMBOLS,
    VARIANTS,
    CrossSectionalTrendConfig,
    TrendPeriod,
    VariantSummary,
    _intersection_histories,
    _simulate_period,
    _summarize,
)
from tradebot.backtest.research_gate import dataset_fingerprint
from tradebot.models import Market


SCHEMA_VERSION = "1.0"
OLDER_V08_REFERENCE = {
    "artifact_digest": "sha256:b87ca2a9fad1cb41fb43d4c3320d826dc1c68e84fd915aebf3de7c1ddeca530c",
    "average_return": 0.004005990055374076,
    "compounded_return": 0.03587744908826518,
    "average_stressed_return": 0.0031970657238009513,
    "accepted": False,
}


@dataclass(frozen=True)
class SparseReplicationConfig:
    history_bars: int = 1800
    holdout_bars: int = 1000
    warmup_bars: int = 180
    test_bars: int = 60
    replication_periods: int = 13
    embargo_bars: int = 40
    fast_lookback: int = 30
    slow_lookback: int = 90
    trend_window: int = 120
    volatility_window: int = 30
    min_market_breadth: float = 0.60
    min_trade_weight: float = 0.05
    extra_cost_per_turnover: float = 0.001
    min_active_periods: int = 6
    min_active_positive_fraction: float = 0.50
    min_beat_buy_hold_fraction: float = 0.50
    min_positive_variants: int = 2
    min_leave_one_out_positive_fraction: float = 0.80
    max_portfolio_drawdown: float = 0.10
    starting_cash: float = 100000.0

    def __post_init__(self) -> None:
        used = self.warmup_bars + self.replication_periods * self.test_bars + self.embargo_bars
        if used != self.holdout_bars:
            raise ValueError("secondary split must preserve warmup, thirteen tests and embargo")
        if self.history_bars != 1800 or self.holdout_bars != 1000:
            raise ValueError("secondary replication requires the frozen 1800/1000 split")
        if self.starting_cash <= 0:
            raise ValueError("starting_cash must be positive")


@dataclass
class SparseVariantResult:
    variant: str
    summary: VariantSummary
    active_positive_fraction: float
    first_half_average_return: float
    second_half_average_return: float


@dataclass
class SparseReplicationReport:
    schema_version: str
    generated_at: str
    market: str
    symbols: list[str]
    full_dataset_fingerprint: str
    replication_dataset_fingerprint: str
    replication_start: str
    replication_end: str
    embargo_start: str
    embargo_end: str
    config: dict[str, Any]
    older_v08_reference: dict[str, Any]
    variants: list[SparseVariantResult]
    leave_one_out_average_returns: dict[str, float]
    leave_one_out_positive_fraction: float
    accepted: bool
    eligible_for_forward_paper_candidate: bool
    reasons: list[str]
    secondary_not_fully_independent: bool = True
    method_parameters_unchanged_from_v08: bool = True
    paper_only: bool = True
    authorizes_real_trading: bool = False


def _load_replication_histories(
    folder: str | Path,
    config: SparseReplicationConfig,
) -> tuple[dict[str, list], dict[str, list]]:
    loader_config = CrossSectionalTrendConfig()
    full = _intersection_histories(folder, loader_config)
    replication = {symbol: candles[-config.holdout_bars :] for symbol, candles in full.items()}
    return full, replication


def _variant_result(
    histories: dict[str, list],
    variant,
    config: SparseReplicationConfig,
) -> SparseVariantResult:
    periods: list[TrendPeriod] = [
        _simulate_period(histories, period, variant, config)  # type: ignore[arg-type]
        for period in range(1, config.replication_periods + 1)
    ]
    summary = _summarize(variant, periods)
    active = [period for period in periods if period.active]
    active_positive = (
        sum(period.net_return > 0 for period in active) / len(active)
        if active
        else 0.0
    )
    first = periods[:6]
    second = periods[6:]
    return SparseVariantResult(
        variant=variant.name,
        summary=summary,
        active_positive_fraction=active_positive,
        first_half_average_return=mean(period.net_return for period in first),
        second_half_average_return=mean(period.net_return for period in second),
    )


def evaluate_sparse_replication(
    folder: str | Path,
    market: Market = Market.CRYPTO,
    config: SparseReplicationConfig | None = None,
) -> SparseReplicationReport:
    if market != Market.CRYPTO:
        raise ValueError("v0.9 replication is frozen to crypto")
    config = config or SparseReplicationConfig()
    full, replication = _load_replication_histories(folder, config)
    variants = [_variant_result(replication, variant, config) for variant in VARIANTS]
    primary = variants[0]
    leave_one_out: dict[str, float] = {}
    for omitted in REQUIRED_SYMBOLS:
        subset = {symbol: candles for symbol, candles in replication.items() if symbol != omitted}
        leave_one_out[omitted] = _variant_result(subset, VARIANTS[0], config).summary.average_return
    leave_positive = sum(value > 0 for value in leave_one_out.values()) / len(leave_one_out)
    positive_variants = sum(
        item.summary.average_return > 0
        and item.summary.compounded_return > 0
        and item.summary.average_stressed_return > 0
        and item.active_positive_fraction >= config.min_active_positive_fraction
        for item in variants
    )
    reasons: list[str] = []
    summary = primary.summary
    if len(summary.periods) != config.replication_periods:
        reasons.append("incomplete_secondary_periods")
    if summary.active_periods < config.min_active_periods:
        reasons.append("too_few_active_secondary_periods")
    if summary.average_return <= 0:
        reasons.append("secondary_average_return_not_positive")
    if summary.compounded_return <= 0:
        reasons.append("secondary_compounded_return_not_positive")
    if summary.average_stressed_return <= 0:
        reasons.append("secondary_stressed_return_not_positive")
    if primary.active_positive_fraction < config.min_active_positive_fraction:
        reasons.append("fewer_than_half_of_active_periods_profitable")
    if primary.first_half_average_return <= 0:
        reasons.append("first_half_secondary_return_not_positive")
    if primary.second_half_average_return <= 0:
        reasons.append("second_half_secondary_return_not_positive")
    if summary.average_excess_vs_buy_and_hold <= 0:
        reasons.append("secondary_did_not_beat_buy_and_hold_on_average")
    if summary.beat_buy_and_hold_fraction < config.min_beat_buy_hold_fraction:
        reasons.append("secondary_did_not_beat_buy_and_hold_often_enough")
    if summary.worst_drawdown > config.max_portfolio_drawdown:
        reasons.append("secondary_drawdown_too_high")
    if positive_variants < config.min_positive_variants:
        reasons.append("secondary_fixed_variants_not_robust")
    if leave_positive < config.min_leave_one_out_positive_fraction:
        reasons.append("secondary_leave_one_asset_out_not_robust")
    accepted = not reasons
    older_positive = (
        float(OLDER_V08_REFERENCE["average_return"]) > 0
        and float(OLDER_V08_REFERENCE["compounded_return"]) > 0
        and float(OLDER_V08_REFERENCE["average_stressed_return"]) > 0
    )
    first_symbol = REQUIRED_SYMBOLS[0]
    test_end_index = config.warmup_bars + config.replication_periods * config.test_bars - 1
    embargo_start_index = test_end_index + 1
    return SparseReplicationReport(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        market=market.value,
        symbols=list(REQUIRED_SYMBOLS),
        full_dataset_fingerprint=dataset_fingerprint(full),
        replication_dataset_fingerprint=dataset_fingerprint(replication),
        replication_start=replication[first_symbol][0].timestamp.isoformat(),
        replication_end=replication[first_symbol][test_end_index].timestamp.isoformat(),
        embargo_start=replication[first_symbol][embargo_start_index].timestamp.isoformat(),
        embargo_end=replication[first_symbol][-1].timestamp.isoformat(),
        config=asdict(config),
        older_v08_reference=dict(OLDER_V08_REFERENCE),
        variants=variants,
        leave_one_out_average_returns=leave_one_out,
        leave_one_out_positive_fraction=leave_positive,
        accepted=accepted,
        eligible_for_forward_paper_candidate=accepted and older_positive,
        reasons=reasons or [
            "Unchanged sparse trend method replicated across both halves and robustness checks."
        ],
    )


def write_report(path: str | Path, report: SparseReplicationReport) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    temporary.replace(output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the unchanged v0.8 sparse trend method on the barred later interval."
    )
    parser.add_argument("--folder", required=True)
    parser.add_argument("--market", choices=[item.value for item in Market], default=Market.CRYPTO.value)
    parser.add_argument("--json-out", default="reports/sparse-trend-replication/sparse_trend_replication.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = evaluate_sparse_replication(args.folder, market=Market(args.market))
    write_report(args.json_out, report)
    print(json.dumps(asdict(report), indent=2))
    return 0 if report.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
