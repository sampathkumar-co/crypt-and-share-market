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

from tradebot.backtest import crypto_factor_risk_overlay as v11
from tradebot.backtest import crypto_multifactor as base
from tradebot.backtest.research_gate import dataset_fingerprint, load_histories
from tradebot.models import Candle, Market

SCHEMA_VERSION = "1.2"
REQUIRED_SYMBOLS = (
    "LTCUSDT",
    "BCHUSDT",
    "LINKUSDT",
    "XLMUSDT",
    "ETCUSDT",
    "ATOMUSDT",
    "UNIUSDT",
    "AAVEUSDT",
)

VARIANTS = (
    base.MultiFactorVariant("primary_discrete_veto", "discrete", 14, 2, 0.20, 0.40, 0.30, 0.0),
    base.MultiFactorVariant("conservative_discrete_veto", "discrete", 21, 2, 0.30, 0.35, 0.24, 0.0),
    base.MultiFactorVariant("diversified_discrete_veto", "discrete", 14, 3, 0.20, 0.30, 0.27, 0.0),
    base.MultiFactorVariant("continuous_factor_risk", "continuous_factor", 14, 2, 0.25, 0.40, 0.25, 0.0),
    base.MultiFactorVariant("continuous_risk_only", "continuous_risk", 14, 2, 0.25, 0.40, 0.25, 0.0),
    base.MultiFactorVariant("raw_simple_trend", "raw_simple", 14, 2, 0.20, 0.40, 0.30, 0.0),
)


@dataclass(frozen=True)
class DiscreteFactorVetoConfig:
    history_bars: int = 2050
    discovery_bars: int = 1800
    holdout_bars: int = 250
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
    max_portfolio_drawdown: float = 0.20
    min_return_retention: float = 0.65
    min_efficiency_improvement: float = 0.20
    min_drawdown_reduction: float = 0.25
    min_positive_discrete_variants: int = 2
    min_unique_selected_assets: int = 4
    min_leave_one_out_positive_fraction: float = 0.75

    def __post_init__(self) -> None:
        if self.discovery_bars + self.holdout_bars != self.history_bars:
            raise ValueError("discovery and holdout must consume exactly 2050 bars")
        used = (
            self.warmup_bars
            + (self.early_periods + self.late_periods) * self.test_bars
            + self.middle_embargo_bars
            + self.final_embargo_bars
        )
        if used != self.discovery_bars:
            raise ValueError("frozen discovery split must consume exactly 1800 bars")
        if self.starting_cash <= 0:
            raise ValueError("starting_cash must be positive")

    @property
    def total_periods(self) -> int:
        return self.early_periods + self.late_periods


@dataclass
class DiscreteFactorVetoReport:
    schema_version: str
    generated_at: str
    market: str
    symbols: list[str]
    full_dataset_fingerprint: str
    discovery_dataset_fingerprint: str
    holdout_dataset_fingerprint: str
    full_start: str
    full_end: str
    discovery_start: str
    discovery_end: str
    holdout_start: str
    holdout_end: str
    config: dict[str, Any]
    variants: list[base.MultiFactorSummary]
    raw_baseline_reproduction_max_abs_error: float
    primary_return_retention: float
    primary_efficiency_improvement: float
    primary_drawdown_reduction: float
    primary_active_median_return: float
    primary_profitable_active_fraction: float
    primary_unique_selected_assets: list[str]
    leave_one_out_average_returns: dict[str, float]
    leave_one_out_positive_fraction: float
    accepted: bool
    eligible_for_holdout_replication: bool
    eligible_for_forward_paper: bool
    reasons: list[str]
    paper_only: bool = True
    authorizes_real_trading: bool = False


def _base_config(config: DiscreteFactorVetoConfig) -> base.CryptoMultiFactorConfig:
    return base.CryptoMultiFactorConfig(
        history_bars=config.discovery_bars,
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


def _aligned_histories(
    folder: str | Path,
    config: DiscreteFactorVetoConfig,
) -> tuple[dict[str, list[Candle]], dict[str, list[Candle]], dict[str, list[Candle]]]:
    loaded = load_histories(folder)
    missing = sorted(set(REQUIRED_SYMBOLS) - set(loaded))
    if missing:
        raise ValueError(f"Missing required histories: {', '.join(missing)}")
    common = sorted(
        set.intersection(*(set(c.timestamp for c in loaded[symbol]) for symbol in REQUIRED_SYMBOLS))
    )
    if len(common) < config.history_bars:
        raise ValueError(f"Only {len(common)} aligned candles are available; {config.history_bars} are required")
    chosen = common[-config.history_bars :]
    chosen_set = set(chosen)
    full: dict[str, list[Candle]] = {}
    for symbol in REQUIRED_SYMBOLS:
        mapping = {c.timestamp: c for c in loaded[symbol] if c.timestamp in chosen_set}
        full[symbol] = [mapping[timestamp] for timestamp in chosen]
        if len(full[symbol]) != config.history_bars:
            raise ValueError(f"{symbol} is incomplete after timestamp alignment")
    discovery = {symbol: candles[: config.discovery_bars] for symbol, candles in full.items()}
    holdout = {symbol: candles[config.discovery_bars :] for symbol, candles in full.items()}
    return full, discovery, holdout


def _raw_variant(variant: base.MultiFactorVariant) -> base.MultiFactorVariant:
    return base.MultiFactorVariant(
        variant.name,
        "simple",
        variant.rebalance_bars,
        variant.top_n,
        variant.min_cash_reserve,
        variant.max_asset_weight,
        variant.target_volatility,
        0.0,
    )


def _market_proxy(prior: dict[str, list[Candle]]) -> str:
    return max(prior, key=lambda symbol: prior[symbol][-1].close * prior[symbol][-1].volume)


def _risk_families(
    selected: list[str],
    prior: dict[str, list[Candle]],
) -> tuple[dict[str, bool], bool]:
    proxy_symbol = _market_proxy(prior)
    proxy = prior[proxy_symbol]
    features = {
        symbol: base._feature_row(candles, proxy)
        for symbol, candles in prior.items()
    }
    scores = base._scores(features, "full")
    selected_rows = [features[symbol] for symbol in selected]
    selected_scores = [scores[symbol] for symbol in selected]
    selected_pair_correlation = v11._selected_average_correlation(selected, prior)
    universe_correlation = base._average_pairwise_correlation(prior)

    families = {
        "factor_quality": mean(selected_scores) < 0.50,
        "tail_risk": (
            min(row["drawdown_90"] for row in selected_rows) <= -0.25
            or mean(row["downside_volatility_60"] for row in selected_rows) >= 0.90
        ),
        "crowding": selected_pair_correlation >= 0.90 or universe_correlation >= 0.88,
        "fragility": (
            median(row["volume_confirmation"] for row in selected_rows) < -0.10
            or mean(row["overextension_7"] for row in selected_rows) > 0.10
        ),
    }
    median_universe_volatility = median(row["volatility_30"] for row in features.values())
    crisis = features[proxy_symbol]["drawdown_90"] <= -0.35 or median_universe_volatility >= 1.40
    return families, crisis


def _scale_raw_weights(raw_weights: dict[str, float], exposure: float) -> dict[str, float]:
    raw_exposure = sum(raw_weights.values())
    if raw_exposure <= 0 or exposure <= 0:
        return {}
    multiplier = min(1.0, exposure / raw_exposure)
    return {symbol: weight * multiplier for symbol, weight in raw_weights.items()}


def _target_weights(
    prior: dict[str, list[Candle]],
    variant: base.MultiFactorVariant,
    drawdown_multiplier: float,
) -> tuple[dict[str, float], str]:
    if variant.factor_set == "raw_simple":
        return base._simple_trend_target(prior, _raw_variant(variant))
    if variant.factor_set in {"continuous_factor", "continuous_risk"}:
        factor_set = "factor_risk" if variant.factor_set == "continuous_factor" else "risk_only"
        continuous = base.MultiFactorVariant(
            variant.name,
            factor_set,
            variant.rebalance_bars,
            variant.top_n,
            variant.min_cash_reserve,
            variant.max_asset_weight,
            variant.target_volatility,
            0.0,
        )
        return v11._overlay_target_weights(prior, continuous, drawdown_multiplier)

    raw_weights, regime = base._simple_trend_target(prior, _raw_variant(variant))
    if not raw_weights or drawdown_multiplier <= 0:
        return {}, regime
    selected = list(raw_weights)
    families, crisis = _risk_families(selected, prior)
    if crisis:
        return {}, "crisis_veto"
    active = sum(families.values())
    raw_exposure = sum(raw_weights.values())
    if active >= 3:
        exposure = min(raw_exposure, 0.30)
    elif active == 2:
        exposure = min(raw_exposure, 0.55)
    else:
        exposure = raw_exposure
    exposure *= drawdown_multiplier
    return _scale_raw_weights(raw_weights, exposure), f"risk_families_{active}"


def _variant_result(
    histories: dict[str, list[Candle]],
    variant: base.MultiFactorVariant,
    config: DiscreteFactorVetoConfig,
) -> base.MultiFactorSummary:
    base_config = _base_config(config)
    with patch.object(base, "_target_weights", _target_weights):
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


def evaluate_discrete_factor_veto(
    folder: str | Path,
    market: Market = Market.CRYPTO,
    config: DiscreteFactorVetoConfig | None = None,
) -> DiscreteFactorVetoReport:
    if market != Market.CRYPTO:
        raise ValueError("v1.2 discrete factor veto is frozen to crypto")
    config = config or DiscreteFactorVetoConfig()
    full, discovery, holdout = _aligned_histories(folder, config)
    base_config = _base_config(config)

    summaries = [_variant_result(discovery, variant, config) for variant in VARIANTS]
    by_name = {summary.variant: summary for summary in summaries}
    primary = by_name["primary_discrete_veto"]
    conservative = by_name["conservative_discrete_veto"]
    diversified = by_name["diversified_discrete_veto"]
    continuous_factor = by_name["continuous_factor_risk"]
    continuous_risk = by_name["continuous_risk_only"]
    raw = by_name["raw_simple_trend"]

    reference = base._variant_result(discovery, base.VARIANTS[-1], base_config)
    reproduction_error = max(
        (abs(left.net_return - right.net_return) for left, right in zip(raw.periods, reference.periods)),
        default=0.0,
    )
    retention = primary.average_return / raw.average_return if raw.average_return > 0 else 0.0
    raw_efficiency = _efficiency(raw)
    efficiency_improvement = _efficiency(primary) / raw_efficiency - 1.0 if raw_efficiency > 0 else 0.0
    drawdown_reduction = 1.0 - primary.worst_drawdown / raw.worst_drawdown if raw.worst_drawdown > 0 else 0.0
    active_median, profitable_active_fraction = _active_statistics(primary)
    unique_assets = sorted({symbol for period in primary.periods for symbol in period.selected_symbols})

    leave_one_out: dict[str, float] = {}
    for omitted in REQUIRED_SYMBOLS:
        subset = {symbol: candles for symbol, candles in discovery.items() if symbol != omitted}
        leave_one_out[omitted] = _variant_result(subset, VARIANTS[0], config).average_return
    leave_positive = sum(value > 0 for value in leave_one_out.values()) / len(leave_one_out)

    positive_discrete_variants = sum(
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
    if primary.average_return <= continuous_factor.average_return:
        reasons.append("does_not_beat_continuous_factor_overlay")
    if continuous_risk.average_return > 0 and primary.average_return < 0.95 * continuous_risk.average_return:
        reasons.append("does_not_retain_continuous_risk_only_return")
    if drawdown_reduction < config.min_drawdown_reduction:
        reasons.append("raw_trend_drawdown_not_reduced_enough")
    if positive_discrete_variants < config.min_positive_discrete_variants:
        reasons.append("too_few_positive_discrete_veto_variants")
    if len(unique_assets) < config.min_unique_selected_assets:
        reasons.append("too_few_distinct_selected_assets")
    if leave_positive < config.min_leave_one_out_positive_fraction:
        reasons.append("leave_one_asset_out_not_robust")

    accepted = not reasons
    first_symbol = next(iter(full))
    return DiscreteFactorVetoReport(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        market=market.value,
        symbols=sorted(full),
        full_dataset_fingerprint=dataset_fingerprint(full),
        discovery_dataset_fingerprint=dataset_fingerprint(discovery),
        holdout_dataset_fingerprint=dataset_fingerprint(holdout),
        full_start=full[first_symbol][0].timestamp.isoformat(),
        full_end=full[first_symbol][-1].timestamp.isoformat(),
        discovery_start=discovery[first_symbol][0].timestamp.isoformat(),
        discovery_end=discovery[first_symbol][-1].timestamp.isoformat(),
        holdout_start=holdout[first_symbol][0].timestamp.isoformat(),
        holdout_end=holdout[first_symbol][-1].timestamp.isoformat(),
        config=asdict(config),
        variants=summaries,
        raw_baseline_reproduction_max_abs_error=reproduction_error,
        primary_return_retention=retention,
        primary_efficiency_improvement=efficiency_improvement,
        primary_drawdown_reduction=drawdown_reduction,
        primary_active_median_return=active_median,
        primary_profitable_active_fraction=profitable_active_fraction,
        primary_unique_selected_assets=unique_assets,
        leave_one_out_average_returns=leave_one_out,
        leave_one_out_positive_fraction=leave_positive,
        accepted=accepted,
        eligible_for_holdout_replication=accepted,
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
    parser = argparse.ArgumentParser(description="Evaluate the frozen crypto discrete factor veto")
    parser.add_argument("--folder", required=True)
    parser.add_argument("--market", choices=[market.value for market in Market], default=Market.CRYPTO.value)
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args(argv)
    report = evaluate_discrete_factor_veto(args.folder, Market(args.market))
    payload = asdict(report)
    _write_json(args.json_out, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
