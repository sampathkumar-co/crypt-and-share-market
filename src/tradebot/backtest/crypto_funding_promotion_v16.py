from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from tradebot.backtest import crypto_multiregime_4h as base
from tradebot.backtest import crypto_multiregime_4h_calendar_funding as calendar
from tradebot.backtest.research_gate import dataset_fingerprint
from tradebot.models import Market

SCHEMA_VERSION = "1.6.1"
STANDARD_COST_PER_TURNOVER = 0.0015
DOUBLE_COST_PER_TURNOVER = 0.0030

PRIMARY_CONFIG = base.MultiRegimeConfig(
    max_positions=2,
    max_asset_weight=0.25,
    min_cash_reserve=0.50,
    target_volatility=0.25,
    max_drawdown=0.08,
)
ORIGINAL_CONFIG = base.MultiRegimeConfig(
    max_positions=3,
    max_asset_weight=0.25,
    min_cash_reserve=0.25,
    target_volatility=0.30,
    max_drawdown=0.08,
)
DEFENSIVE_CONFIG = base.MultiRegimeConfig(
    max_positions=2,
    max_asset_weight=0.125,
    min_cash_reserve=0.75,
    target_volatility=0.20,
    max_drawdown=0.08,
)

PROFILE_CONFIGS = (
    ("primary_balanced", PRIMARY_CONFIG),
    ("original_exposure", ORIGINAL_CONFIG),
    ("defensive_exposure", DEFENSIVE_CONFIG),
)


@dataclass
class PromotionProfileResult:
    profile: str
    config: dict[str, Any]
    summary: base.MultiRegimeSummary
    average_double_cost_stressed_return: float


@dataclass
class FundingPromotionReport:
    schema_version: str
    generated_at: str
    mode: str
    market: str
    symbols: list[str]
    price_dataset_fingerprint: str
    external_manifest_fingerprint: str
    dataset_start: str
    dataset_end: str
    discovery_test_start: str
    discovery_test_end: str
    embargo_start: str
    embargo_end: str
    holdout_start: str
    holdout_end: str
    profiles: list[PromotionProfileResult]
    baselines: list[base.MultiRegimeSummary]
    leave_one_asset_out_average_returns: dict[str, float]
    leave_one_asset_out_positive_count: int
    leave_one_period_out_average_returns: dict[str, float]
    leave_one_period_out_positive_count: int
    positive_sizing_profiles: int
    accepted: bool
    eligible_for_holdout: bool
    eligible_for_shadow_paper: bool
    eligible_for_forward_paper: bool
    reasons: list[str]
    paper_only: bool = True
    authorizes_real_trading: bool = False


def _funding_variant(name: str) -> base.MultiRegimeVariant:
    return base.MultiRegimeVariant(name, ("funding",))


def _profile_summary(
    histories: dict[str, list[base.Candle]],
    store: base.ExternalStore,
    profile: str,
    config: base.MultiRegimeConfig,
    mode: str,
) -> PromotionProfileResult:
    count = config.discovery_periods if mode == "discovery" else config.holdout_periods
    variant = _funding_variant(profile)
    with calendar.calendar_funding_model():
        periods = [
            base._simulate_period(histories, store, variant, period, config, mode)
            for period in range(1, count + 1)
        ]
    summary = base._summarize(profile, periods)
    double_stressed = mean(
        period.net_return - period.turnover * DOUBLE_COST_PER_TURNOVER
        for period in periods
    ) if periods else 0.0
    return PromotionProfileResult(
        profile=profile,
        config=asdict(config),
        summary=summary,
        average_double_cost_stressed_return=double_stressed,
    )


def _baselines(
    histories: dict[str, list[base.Candle]],
    config: base.MultiRegimeConfig,
    mode: str,
) -> list[base.MultiRegimeSummary]:
    return [
        base._pseudo_summary(histories, config, mode, "cash"),
        base._pseudo_summary(histories, config, mode, "equal_weight_buy_hold"),
    ]


def _leave_one_period_out(periods: list[base.MultiRegimePeriod]) -> dict[str, float]:
    output: dict[str, float] = {}
    for omitted in range(len(periods)):
        retained = [
            period.net_return
            for index, period in enumerate(periods)
            if index != omitted
        ]
        output[f"period_{omitted + 1}"] = mean(retained) if retained else 0.0
    return output


def _report(
    histories: dict[str, list[base.Candle]],
    store: base.ExternalStore,
    profiles: list[PromotionProfileResult],
    baselines: list[base.MultiRegimeSummary],
    mode: str,
    reasons: list[str],
    leave_one_asset: dict[str, float],
    leave_one_period: dict[str, float],
) -> FundingPromotionReport:
    bounds = base._date_boundaries(histories, PRIMARY_CONFIG)
    accepted = not reasons
    positive_profiles = sum(
        result.summary.average_return > 0
        and result.summary.average_stressed_return > 0
        and result.average_double_cost_stressed_return > 0
        for result in profiles
    )
    return FundingPromotionReport(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        mode=mode,
        market=Market.CRYPTO.value,
        symbols=sorted(histories),
        price_dataset_fingerprint=dataset_fingerprint(histories),
        external_manifest_fingerprint=store.manifest_fingerprint,
        dataset_start=histories[next(iter(histories))][0].timestamp.isoformat(),
        dataset_end=histories[next(iter(histories))][-1].timestamp.isoformat(),
        profiles=profiles,
        baselines=baselines,
        leave_one_asset_out_average_returns=leave_one_asset,
        leave_one_asset_out_positive_count=sum(value > 0 for value in leave_one_asset.values()),
        leave_one_period_out_average_returns=leave_one_period,
        leave_one_period_out_positive_count=sum(value > 0 for value in leave_one_period.values()),
        positive_sizing_profiles=positive_profiles,
        accepted=accepted,
        eligible_for_holdout=accepted if mode == "promotion_audit" else False,
        eligible_for_shadow_paper=accepted if mode == "holdout" else False,
        eligible_for_forward_paper=False,
        reasons=reasons,
        **bounds,
    )


def evaluate_promotion_audit(
    price_folder: str | Path,
    external_folder: str | Path,
    market: Market = Market.CRYPTO,
) -> FundingPromotionReport:
    if market != Market.CRYPTO:
        raise ValueError("v1.6.1 is frozen to crypto")
    histories = base.load_exact_histories(price_folder, PRIMARY_CONFIG)
    store = base.load_external_store(external_folder)
    profiles = [
        _profile_summary(histories, store, profile, config, "discovery")
        for profile, config in PROFILE_CONFIGS
    ]
    baselines = _baselines(histories, PRIMARY_CONFIG, "discovery")
    by_profile = {result.profile: result for result in profiles}
    by_baseline = {summary.variant: summary for summary in baselines}
    primary = by_profile["primary_balanced"]
    summary = primary.summary

    leave_one_asset: dict[str, float] = {}
    for omitted in base.REQUIRED_SYMBOLS:
        subset = {
            symbol: candles
            for symbol, candles in histories.items()
            if symbol != omitted
        }
        leave_one_asset[omitted] = _profile_summary(
            subset,
            store,
            "primary_balanced",
            PRIMARY_CONFIG,
            "discovery",
        ).summary.average_return
    leave_one_period = _leave_one_period_out(summary.periods)

    reasons: list[str] = []
    if len(summary.periods) != PRIMARY_CONFIG.discovery_periods:
        reasons.append("incomplete_audit_periods")
    if summary.active_periods != PRIMARY_CONFIG.discovery_periods:
        reasons.append("not_all_audit_periods_active")
    if summary.positive_periods < 4:
        reasons.append("too_few_profitable_periods")
    if summary.average_return <= 0:
        reasons.append("average_return_not_positive")
    if summary.median_return <= 0:
        reasons.append("median_return_not_positive")
    if summary.compounded_return <= 0:
        reasons.append("compounded_return_not_positive")
    if summary.first_half_average <= 0:
        reasons.append("first_half_not_positive")
    if summary.second_half_average <= 0:
        reasons.append("second_half_not_positive")
    if summary.average_stressed_return <= 0:
        reasons.append("standard_cost_stress_not_positive")
    if primary.average_double_cost_stressed_return <= 0:
        reasons.append("double_cost_stress_not_positive")
    if summary.average_return <= by_baseline["cash"].average_return:
        reasons.append("does_not_beat_cash")
    if summary.average_return <= by_baseline["equal_weight_buy_hold"].average_return:
        reasons.append("does_not_beat_equal_weight_average")
    if summary.worst_drawdown > PRIMARY_CONFIG.max_drawdown:
        reasons.append("drawdown_too_high")
    if len(summary.selected_symbols) < 6:
        reasons.append("too_few_distinct_assets")
    if summary.max_asset_notional_fraction > 0.35:
        reasons.append("asset_notional_concentration_too_high")
    if sum(value > 0 for value in leave_one_period.values()) != 6:
        reasons.append("leave_one_period_out_not_robust")
    if sum(value > 0 for value in leave_one_asset.values()) < 6:
        reasons.append("leave_one_asset_out_not_robust")
    for profile in ("original_exposure", "defensive_exposure"):
        result = by_profile[profile]
        if result.summary.average_return <= 0:
            reasons.append(f"{profile}_average_not_positive")
        if result.summary.average_stressed_return <= 0:
            reasons.append(f"{profile}_standard_stress_not_positive")
        if result.average_double_cost_stressed_return <= 0:
            reasons.append(f"{profile}_double_stress_not_positive")

    return _report(
        histories,
        store,
        profiles,
        baselines,
        "promotion_audit",
        reasons,
        leave_one_asset,
        leave_one_period,
    )


def evaluate_holdout(
    price_folder: str | Path,
    external_folder: str | Path,
    promotion_json: str | Path,
    market: Market = Market.CRYPTO,
) -> FundingPromotionReport:
    if market != Market.CRYPTO:
        raise ValueError("v1.6.1 is frozen to crypto")
    promotion = json.loads(Path(promotion_json).read_text(encoding="utf-8"))
    if promotion.get("accepted") is not True or promotion.get("eligible_for_holdout") is not True:
        raise ValueError("v1.6.1 holdout is locked because promotion audit did not pass")
    histories = base.load_exact_histories(price_folder, PRIMARY_CONFIG)
    store = base.load_external_store(external_folder)
    if promotion.get("price_dataset_fingerprint") != dataset_fingerprint(histories):
        raise ValueError("v1.6.1 holdout price fingerprint changed")
    if promotion.get("external_manifest_fingerprint") != store.manifest_fingerprint:
        raise ValueError("v1.6.1 holdout external fingerprint changed")

    primary = _profile_summary(
        histories,
        store,
        "primary_balanced",
        PRIMARY_CONFIG,
        "holdout",
    )
    profiles = [primary]
    baselines = _baselines(histories, PRIMARY_CONFIG, "holdout")
    by_baseline = {summary.variant: summary for summary in baselines}
    summary = primary.summary
    reasons: list[str] = []
    if summary.active_periods < 2:
        reasons.append("too_few_active_holdout_periods")
    if summary.positive_periods < 2:
        reasons.append("too_few_profitable_holdout_periods")
    if summary.average_return <= 0:
        reasons.append("holdout_average_not_positive")
    if summary.compounded_return <= 0:
        reasons.append("holdout_compounded_not_positive")
    if summary.average_stressed_return <= 0:
        reasons.append("holdout_standard_stress_not_positive")
    if primary.average_double_cost_stressed_return <= 0:
        reasons.append("holdout_double_stress_not_positive")
    if summary.average_return <= by_baseline["cash"].average_return:
        reasons.append("holdout_does_not_beat_cash")
    if summary.average_return <= by_baseline["equal_weight_buy_hold"].average_return:
        reasons.append("holdout_does_not_beat_equal_weight")
    if summary.worst_drawdown > PRIMARY_CONFIG.max_drawdown:
        reasons.append("holdout_drawdown_too_high")
    if len(summary.selected_symbols) < 3:
        reasons.append("holdout_too_few_assets")
    if summary.max_asset_notional_fraction > 0.45:
        reasons.append("holdout_asset_concentration_too_high")
    return _report(
        histories,
        store,
        profiles,
        baselines,
        "holdout",
        reasons,
        {},
        {},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit frozen v1.6.1 funding candidate promotion"
    )
    parser.add_argument("--price-folder", required=True)
    parser.add_argument("--external-folder", required=True)
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--mode", choices=("promotion_audit", "holdout"), default="promotion_audit")
    parser.add_argument("--promotion-json")
    args = parser.parse_args(argv)
    if args.mode == "holdout":
        if not args.promotion_json:
            raise SystemExit("--promotion-json is required for holdout mode")
        report = evaluate_holdout(
            args.price_folder,
            args.external_folder,
            args.promotion_json,
        )
    else:
        report = evaluate_promotion_audit(
            args.price_folder,
            args.external_folder,
        )
    payload = asdict(report)
    base._write_json(args.json_out, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
