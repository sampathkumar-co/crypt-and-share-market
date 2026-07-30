from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any
from unittest.mock import patch

from tradebot.backtest import crypto_multifactor as base
from tradebot.backtest.research_gate import dataset_fingerprint
from tradebot.models import Candle, Market

SCHEMA_VERSION = "1.1"
REQUIRED_SYMBOLS = base.REQUIRED_SYMBOLS

VARIANTS = (
    base.MultiFactorVariant("primary_factor_risk", "factor_risk", 14, 2, 0.25, 0.40, 0.25, 0.0),
    base.MultiFactorVariant("conservative_factor_risk", "factor_risk", 21, 2, 0.40, 0.32, 0.18, 0.0),
    base.MultiFactorVariant("diversified_factor_risk", "factor_risk", 14, 3, 0.30, 0.30, 0.22, 0.0),
    base.MultiFactorVariant("risk_only_ablation", "risk_only", 14, 2, 0.25, 0.40, 0.25, 0.0),
    base.MultiFactorVariant("raw_simple_trend", "raw_simple", 14, 2, 0.20, 0.40, 0.30, 0.0),
)


@dataclass(frozen=True)
class FactorRiskOverlayConfig:
    history_bars: int = 1800
    warmup_bars: int = 240
    early_periods: int = 6
    late_periods: int = 6
    test_bars: int = 120
    middle_embargo_bars: int = 30
    final_embargo_bars: int = 90
    min_trade_weight: float = 0.04
    extra_cost_per_turnover: float = 0.0015
    starting_cash: float = 100000.0
    min_active_periods: int = 8
    min_profitable_active_fraction: float = 0.55
    max_portfolio_drawdown: float = 0.15
    min_return_retention: float = 0.60
    min_efficiency_improvement: float = 0.20
    min_positive_factor_variants: int = 2
    min_leave_one_out_positive_fraction: float = 0.80

    def __post_init__(self) -> None:
        used = (
            self.warmup_bars
            + (self.early_periods + self.late_periods) * self.test_bars
            + self.middle_embargo_bars
            + self.final_embargo_bars
        )
        if used != self.history_bars:
            raise ValueError("frozen split must consume exactly 1800 bars")
        if self.starting_cash <= 0:
            raise ValueError("starting_cash must be positive")
        if not 0 <= self.min_trade_weight < 1:
            raise ValueError("min_trade_weight must be between zero and one")

    @property
    def total_periods(self) -> int:
        return self.early_periods + self.late_periods


@dataclass
class FactorRiskOverlayReport:
    schema_version: str
    generated_at: str
    market: str
    symbols: list[str]
    dataset_fingerprint: str
    dataset_start: str
    dataset_end: str
    config: dict[str, Any]
    variants: list[base.MultiFactorSummary]
    raw_baseline_reference_average_return: float
    raw_baseline_reproduction_max_abs_error: float
    primary_return_retention: float
    primary_efficiency_improvement: float
    primary_active_median_return: float
    primary_profitable_active_fraction: float
    factor_value_condition_passed: bool
    leave_one_out_average_returns: dict[str, float]
    leave_one_out_positive_fraction: float
    accepted: bool
    eligible_for_independent_replication: bool
    eligible_for_forward_paper: bool
    reasons: list[str]
    paper_only: bool = True
    authorizes_real_trading: bool = False


def _base_config(config: FactorRiskOverlayConfig) -> base.CryptoMultiFactorConfig:
    return base.CryptoMultiFactorConfig(
        history_bars=config.history_bars,
        warmup_bars=config.warmup_bars,
        early_periods=config.early_periods,
        late_periods=config.late_periods,
        test_bars=config.test_bars,
        middle_embargo_bars=config.middle_embargo_bars,
        final_embargo_bars=config.final_embargo_bars,
        min_trade_weight=config.min_trade_weight,
        extra_cost_per_turnover=config.extra_cost_per_turnover,
        starting_cash=config.starting_cash,
    )


def _trend_rows(prior: dict[str, list[Candle]]) -> dict[str, dict[str, float]]:
    rows: dict[str, dict[str, float]] = {}
    for symbol, candles in prior.items():
        close = candles[-1].close
        ma120 = mean(c.close for c in candles[-120:])
        fast = base._simple_return(candles, 30)
        slow = base._simple_return(candles, 90)
        volatility = base._annualized_volatility(candles, 30)
        rows[symbol] = {
            "fast": fast,
            "slow": slow,
            "volatility": volatility,
            "trend": 1.0 if close > ma120 and fast > 0 and slow > 0 else 0.0,
            "score": (0.40 * fast + 0.60 * slow) / max(volatility, 0.10),
        }
    return rows


def _factor_exposure_multiplier(score: float) -> float:
    return max(0.55, min(1.0, 0.55 + 0.45 * score))


def _selected_average_correlation(selected: list[str], prior: dict[str, list[Candle]]) -> float:
    correlations: list[float] = []
    for index, left in enumerate(selected):
        for right in selected[index + 1 :]:
            correlations.append(base._pair_correlation(left, right, prior))
    return mean(correlations) if correlations else 0.0


def _correlation_multiplier(correlation: float) -> float:
    if correlation >= 0.92:
        return 0.65
    if correlation >= 0.85:
        return 0.80
    return 1.0


def _overlay_target_weights(
    prior: dict[str, list[Candle]],
    variant: base.MultiFactorVariant,
    drawdown_multiplier: float,
) -> tuple[dict[str, float], str]:
    if variant.factor_set == "raw_simple":
        raw_variant = base.MultiFactorVariant(
            "raw_simple_trend",
            "simple",
            variant.rebalance_bars,
            variant.top_n,
            variant.min_cash_reserve,
            variant.max_asset_weight,
            variant.target_volatility,
            0.0,
        )
        return base._simple_trend_target(prior, raw_variant)

    rows = _trend_rows(prior)
    breadth = sum(row["trend"] > 0 for row in rows.values()) / len(rows)
    proxy = rows.get("BTCUSDT") or rows[
        max(prior, key=lambda symbol: prior[symbol][-1].close * prior[symbol][-1].volume)
    ]
    healthy = breadth >= 0.60 and median(row["slow"] for row in rows.values()) > 0 and proxy["trend"] > 0
    if not healthy or drawdown_multiplier <= 0:
        return {}, "cash"

    ranked = sorted(
        ((symbol, row) for symbol, row in rows.items() if row["trend"] > 0 and row["score"] > 0),
        key=lambda item: (item[1]["score"], item[0]),
        reverse=True,
    )[: variant.top_n]
    if not ranked:
        return {}, "cash"

    selected_symbols = [symbol for symbol, _ in ranked]
    btc = prior.get("BTCUSDT") or prior[next(iter(prior))]
    features = {symbol: base._feature_row(candles, btc) for symbol, candles in prior.items()}
    factor_scores = base._scores(features, "full")
    average_score = mean(factor_scores[symbol] for symbol in selected_symbols)

    raw: dict[str, float] = {}
    for symbol, row in ranked:
        quality = 0.50 + 0.50 * factor_scores[symbol] if variant.factor_set == "factor_risk" else 1.0
        raw[symbol] = quality / max(row["volatility"], 0.05)

    unit_total = sum(raw.values())
    unit = {symbol: value / unit_total for symbol, value in raw.items() if unit_total > 0}
    estimated_volatility = base._portfolio_volatility(unit, prior)
    exposure = (1.0 - variant.min_cash_reserve) * drawdown_multiplier
    exposure *= _correlation_multiplier(_selected_average_correlation(selected_symbols, prior))
    if estimated_volatility > 0:
        exposure = min(exposure, variant.target_volatility / estimated_volatility)
    if variant.factor_set == "factor_risk":
        exposure *= _factor_exposure_multiplier(average_score)

    return (
        base._capped_weights(raw, exposure, variant.max_asset_weight),
        "factor_risk" if variant.factor_set == "factor_risk" else "risk_only",
    )


def _variant_result(
    histories: dict[str, list[Candle]],
    variant: base.MultiFactorVariant,
    config: FactorRiskOverlayConfig,
) -> base.MultiFactorSummary:
    base_config = _base_config(config)
    with patch.object(base, "_target_weights", _overlay_target_weights):
        periods = [
            base._simulate_period(histories, period, variant, base_config)
            for period in range(1, config.total_periods + 1)
        ]
    return base._summarize(variant, periods)


def _active_statistics(summary: base.MultiFactorSummary) -> tuple[float, float]:
    active = [period.net_return for period in summary.periods if period.active]
    if not active:
        return 0.0, 0.0
    return median(active), sum(value > 0 for value in active) / len(active)


def _efficiency(summary: base.MultiFactorSummary) -> float:
    return summary.average_return / max(summary.worst_drawdown, 1e-12)


def evaluate_factor_risk_overlay(
    folder: str | Path,
    market: Market = Market.CRYPTO,
    config: FactorRiskOverlayConfig | None = None,
) -> FactorRiskOverlayReport:
    if market != Market.CRYPTO:
        raise ValueError("v1.1 factor-risk overlay is frozen to crypto")
    config = config or FactorRiskOverlayConfig()
    base_config = _base_config(config)
    histories = base._intersection_histories(folder, base_config)

    summaries = [_variant_result(histories, variant, config) for variant in VARIANTS]
    by_name = {summary.variant: summary for summary in summaries}
    primary = by_name["primary_factor_risk"]
    conservative = by_name["conservative_factor_risk"]
    diversified = by_name["diversified_factor_risk"]
    risk_only = by_name["risk_only_ablation"]
    raw = by_name["raw_simple_trend"]

    reference = base._variant_result(histories, base.VARIANTS[-1], base_config)
    reproduction_error = max(
        (abs(left.net_return - right.net_return) for left, right in zip(raw.periods, reference.periods)),
        default=0.0,
    )
    retention = primary.average_return / raw.average_return if raw.average_return > 0 else 0.0
    raw_efficiency = _efficiency(raw)
    primary_efficiency = _efficiency(primary)
    efficiency_improvement = primary_efficiency / raw_efficiency - 1.0 if raw_efficiency > 0 else 0.0
    active_median, profitable_active_fraction = _active_statistics(primary)
    factor_value_passed = (
        primary.average_return > risk_only.average_return
        or (
            primary.average_return >= 0.95 * risk_only.average_return
            and primary.worst_drawdown <= 0.90 * risk_only.worst_drawdown
        )
    )

    leave_one_out: dict[str, float] = {}
    for omitted in REQUIRED_SYMBOLS:
        subset = {symbol: candles for symbol, candles in histories.items() if symbol != omitted}
        leave_one_out[omitted] = _variant_result(subset, VARIANTS[0], config).average_return
    leave_positive = sum(value > 0 for value in leave_one_out.values()) / len(leave_one_out)

    positive_factor_variants = sum(
        summary.average_return > 0
        and summary.compounded_return > 0
        and summary.average_stressed_return > 0
        for summary in (primary, conservative, diversified)
    )

    reasons: list[str] = []
    if reproduction_error > 1e-12:
        reasons.append("raw_simple_trend_baseline_not_reproduced")
    if primary.active_periods < config.min_active_periods:
        reasons.append("too_few_active_periods")
    if primary.average_return <= 0:
        reasons.append("average_return_not_positive")
    if primary.compounded_return <= 0:
        reasons.append("compounded_return_not_positive")
    if primary.average_stressed_return <= 0:
        reasons.append("extra_cost_stressed_return_not_positive")
    if primary.early_average_return <= 0:
        reasons.append("early_half_not_positive")
    if primary.late_average_return <= 0:
        reasons.append("late_half_not_positive")
    if active_median <= 0:
        reasons.append("active_period_median_not_positive")
    if profitable_active_fraction < config.min_profitable_active_fraction:
        reasons.append("too_few_profitable_active_periods")
    if primary.worst_drawdown > config.max_portfolio_drawdown:
        reasons.append("drawdown_too_high")
    if retention < config.min_return_retention:
        reasons.append("retains_too_little_raw_trend_return")
    if efficiency_improvement < config.min_efficiency_improvement:
        reasons.append("return_to_drawdown_efficiency_not_improved_enough")
    if not factor_value_passed:
        reasons.append("factor_score_adds_no_value_beyond_risk_controls")
    if positive_factor_variants < config.min_positive_factor_variants:
        reasons.append("too_few_positive_factor_risk_variants")
    if leave_positive < config.min_leave_one_out_positive_fraction:
        reasons.append("leave_one_asset_out_not_robust")

    accepted = not reasons
    first = histories[next(iter(histories))][0].timestamp.isoformat()
    last = histories[next(iter(histories))][-1].timestamp.isoformat()
    return FactorRiskOverlayReport(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        market=market.value,
        symbols=sorted(histories),
        dataset_fingerprint=dataset_fingerprint(histories),
        dataset_start=first,
        dataset_end=last,
        config=asdict(config),
        variants=summaries,
        raw_baseline_reference_average_return=reference.average_return,
        raw_baseline_reproduction_max_abs_error=reproduction_error,
        primary_return_retention=retention,
        primary_efficiency_improvement=efficiency_improvement,
        primary_active_median_return=active_median,
        primary_profitable_active_fraction=profitable_active_fraction,
        factor_value_condition_passed=factor_value_passed,
        leave_one_out_average_returns=leave_one_out,
        leave_one_out_positive_fraction=leave_positive,
        accepted=accepted,
        eligible_for_independent_replication=accepted,
        eligible_for_forward_paper=False,
        reasons=reasons,
    )


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(destination)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the frozen crypto factor-risk overlay")
    parser.add_argument("--folder", required=True)
    parser.add_argument("--market", choices=[market.value for market in Market], default=Market.CRYPTO.value)
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args(argv)
    report = evaluate_factor_risk_overlay(args.folder, Market(args.market))
    payload = asdict(report)
    _write_json(args.json_out, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
