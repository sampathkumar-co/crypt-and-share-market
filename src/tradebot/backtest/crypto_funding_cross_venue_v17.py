from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from tradebot.backtest import crypto_multiregime_4h as base
from tradebot.backtest import crypto_multiregime_4h_calendar_funding as calendar
from tradebot.backtest.research_gate import dataset_fingerprint, load_histories
from tradebot.data.hyperliquid_4h_provider_v17 import (
    EXPECTED_END_EXCLUSIVE,
    EXPECTED_ROWS,
    EXPECTED_START,
    FOUR_HOURS,
    SYMBOL_TO_COIN,
)
from tradebot.models import Candle, Market

SCHEMA_VERSION = "1.7.1"
DOUBLE_COST_PER_TURNOVER = 0.0030

PRIMARY_CONFIG = base.MultiRegimeConfig(
    total_bars=3_504,
    warmup_bars=600,
    discovery_periods=5,
    discovery_test_bars=390,
    embargo_bars=234,
    holdout_bars=720,
    holdout_periods=3,
    holdout_test_bars=240,
    max_positions=2,
    max_asset_weight=0.25,
    min_cash_reserve=0.50,
    target_volatility=0.25,
    max_drawdown=0.08,
)
ORIGINAL_CONFIG = base.MultiRegimeConfig(
    total_bars=3_504,
    warmup_bars=600,
    discovery_periods=5,
    discovery_test_bars=390,
    embargo_bars=234,
    holdout_bars=720,
    holdout_periods=3,
    holdout_test_bars=240,
    max_positions=3,
    max_asset_weight=0.25,
    min_cash_reserve=0.25,
    target_volatility=0.30,
    max_drawdown=0.08,
)
DEFENSIVE_CONFIG = base.MultiRegimeConfig(
    total_bars=3_504,
    warmup_bars=600,
    discovery_periods=5,
    discovery_test_bars=390,
    embargo_bars=234,
    holdout_bars=720,
    holdout_periods=3,
    holdout_test_bars=240,
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
class CrossVenueProfileResult:
    profile: str
    config: dict[str, Any]
    summary: base.MultiRegimeSummary
    first_block_average: float
    second_block_average: float
    average_double_cost_stressed_return: float


@dataclass
class CrossVenueReport:
    schema_version: str
    generated_at: str
    mode: str
    market: str
    symbols: list[str]
    price_dataset_fingerprint: str
    external_manifest_fingerprint: str
    dataset_start: str
    dataset_end: str
    replication_test_start: str
    replication_test_end: str
    embargo_start: str
    embargo_end: str
    holdout_start: str
    holdout_end: str
    profiles: list[CrossVenueProfileResult]
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


def _expected_timestamps() -> list[datetime]:
    output = []
    cursor = EXPECTED_START
    while cursor < EXPECTED_END_EXCLUSIVE:
        output.append(cursor)
        cursor += FOUR_HOURS
    if len(output) != EXPECTED_ROWS:
        raise ValueError("v1.7.1 expected grid changed")
    return output


def load_cross_venue_histories(
    folder: str | Path,
    config: base.MultiRegimeConfig | None = None,
) -> dict[str, list[Candle]]:
    config = config or PRIMARY_CONFIG
    loaded = load_histories(folder)
    missing_symbols = sorted(set(SYMBOL_TO_COIN) - set(loaded))
    if missing_symbols:
        raise ValueError(
            f"Missing v1.7.1 histories: {', '.join(missing_symbols)}"
        )
    expected = _expected_timestamps()
    aligned: dict[str, list[Candle]] = {}
    for symbol in SYMBOL_TO_COIN:
        mapping = {
            candle.timestamp: candle
            for candle in loaded[symbol]
            if EXPECTED_START <= candle.timestamp < EXPECTED_END_EXCLUSIVE
        }
        missing = [stamp for stamp in expected if stamp not in mapping]
        if missing:
            raise ValueError(
                f"{symbol} is missing {len(missing)} v1.7.1 timestamps; "
                f"first missing {missing[0].isoformat()}"
            )
        aligned[symbol] = [mapping[stamp] for stamp in expected]
        if len(aligned[symbol]) != config.total_bars:
            raise ValueError(
                f"{symbol} has {len(aligned[symbol])} aligned bars; "
                f"{config.total_bars} required"
            )
    return aligned


def _funding_variant(name: str) -> base.MultiRegimeVariant:
    return base.MultiRegimeVariant(name, ("funding",))


def _profile_summary(
    histories: dict[str, list[Candle]],
    store: base.ExternalStore,
    profile: str,
    config: base.MultiRegimeConfig,
    mode: str,
) -> CrossVenueProfileResult:
    count = config.discovery_periods if mode == "discovery" else config.holdout_periods
    variant = _funding_variant(profile)
    with calendar.calendar_funding_model():
        periods = [
            base._simulate_period(histories, store, variant, period, config, mode)
            for period in range(1, count + 1)
        ]
    summary = base._summarize(profile, periods)
    if mode == "discovery":
        first_values = [item.net_return for item in periods[:2]]
        second_values = [item.net_return for item in periods[2:]]
    else:
        split = max(1, len(periods) // 2)
        first_values = [item.net_return for item in periods[:split]]
        second_values = [item.net_return for item in periods[split:]]
    double_stressed = mean(
        item.net_return - item.turnover * DOUBLE_COST_PER_TURNOVER
        for item in periods
    ) if periods else 0.0
    return CrossVenueProfileResult(
        profile=profile,
        config=asdict(config),
        summary=summary,
        first_block_average=mean(first_values) if first_values else 0.0,
        second_block_average=mean(second_values) if second_values else 0.0,
        average_double_cost_stressed_return=double_stressed,
    )


def _baselines(
    histories: dict[str, list[Candle]],
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
            item.net_return
            for index, item in enumerate(periods)
            if index != omitted
        ]
        output[f"period_{omitted + 1}"] = mean(retained) if retained else 0.0
    return output


def _bounds(histories: dict[str, list[Candle]]) -> dict[str, str]:
    candles = histories[next(iter(histories))]
    first_start, _ = base._period_bounds(1, PRIMARY_CONFIG, "discovery")
    _, last_end = base._period_bounds(
        PRIMARY_CONFIG.discovery_periods,
        PRIMARY_CONFIG,
        "discovery",
    )
    return {
        "replication_test_start": candles[first_start].timestamp.isoformat(),
        "replication_test_end": candles[last_end].timestamp.isoformat(),
        "embargo_start": candles[PRIMARY_CONFIG.embargo_start].timestamp.isoformat(),
        "embargo_end": candles[PRIMARY_CONFIG.holdout_start - 1].timestamp.isoformat(),
        "holdout_start": candles[PRIMARY_CONFIG.holdout_start].timestamp.isoformat(),
        "holdout_end": candles[-1].timestamp.isoformat(),
    }


def _report(
    histories: dict[str, list[Candle]],
    store: base.ExternalStore,
    profiles: list[CrossVenueProfileResult],
    baselines: list[base.MultiRegimeSummary],
    mode: str,
    reasons: list[str],
    leave_one_asset: dict[str, float],
    leave_one_period: dict[str, float],
) -> CrossVenueReport:
    accepted = not reasons
    positive_profiles = sum(
        result.summary.average_return > 0
        and result.summary.average_stressed_return > 0
        and result.average_double_cost_stressed_return > 0
        for result in profiles
    )
    return CrossVenueReport(
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
        eligible_for_holdout=accepted if mode == "replication" else False,
        eligible_for_shadow_paper=accepted if mode == "holdout" else False,
        eligible_for_forward_paper=False,
        reasons=reasons,
        **_bounds(histories),
    )


def evaluate_replication(
    price_folder: str | Path,
    external_folder: str | Path,
    market: Market = Market.CRYPTO,
) -> CrossVenueReport:
    if market != Market.CRYPTO:
        raise ValueError("v1.7.1 is frozen to crypto")
    histories = load_cross_venue_histories(price_folder, PRIMARY_CONFIG)
    store = base.load_external_store(external_folder)
    profiles = [
        _profile_summary(histories, store, profile, config, "discovery")
        for profile, config in PROFILE_CONFIGS
    ]
    baselines = _baselines(histories, PRIMARY_CONFIG, "discovery")
    by_profile = {item.profile: item for item in profiles}
    by_baseline = {item.variant: item for item in baselines}
    primary = by_profile["primary_balanced"]
    summary = primary.summary

    leave_one_asset: dict[str, float] = {}
    for omitted in SYMBOL_TO_COIN:
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
        reasons.append("incomplete_replication_periods")
    if summary.active_periods != PRIMARY_CONFIG.discovery_periods:
        reasons.append("not_all_replication_periods_active")
    if summary.positive_periods < 3:
        reasons.append("too_few_profitable_periods")
    if summary.average_return <= 0:
        reasons.append("average_return_not_positive")
    if summary.median_return <= 0:
        reasons.append("median_return_not_positive")
    if summary.compounded_return <= 0:
        reasons.append("compounded_return_not_positive")
    if primary.first_block_average <= 0:
        reasons.append("first_block_not_positive")
    if primary.second_block_average <= 0:
        reasons.append("second_block_not_positive")
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
    if sum(value > 0 for value in leave_one_period.values()) != 5:
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
        "replication",
        reasons,
        leave_one_asset,
        leave_one_period,
    )


def evaluate_holdout(
    price_folder: str | Path,
    external_folder: str | Path,
    replication_json: str | Path,
    expected_price_fingerprint: str,
    market: Market = Market.CRYPTO,
) -> CrossVenueReport:
    if market != Market.CRYPTO:
        raise ValueError("v1.7.1 is frozen to crypto")
    replication = json.loads(Path(replication_json).read_text(encoding="utf-8"))
    if replication.get("accepted") is not True or replication.get("eligible_for_holdout") is not True:
        raise ValueError("v1.7.1 holdout is locked because replication did not pass")
    histories = load_cross_venue_histories(price_folder, PRIMARY_CONFIG)
    store = base.load_external_store(external_folder)
    actual_fingerprint = dataset_fingerprint(histories)
    if actual_fingerprint != expected_price_fingerprint:
        raise ValueError("v1.7.1 holdout price fingerprint changed")
    if replication.get("external_manifest_fingerprint") != store.manifest_fingerprint:
        raise ValueError("v1.7.1 holdout external fingerprint changed")

    primary = _profile_summary(
        histories,
        store,
        "primary_balanced",
        PRIMARY_CONFIG,
        "holdout",
    )
    baselines = _baselines(histories, PRIMARY_CONFIG, "holdout")
    by_baseline = {item.variant: item for item in baselines}
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
        [primary],
        baselines,
        "holdout",
        reasons,
        {},
        {},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate frozen v1.7.1 funding cross-venue replication"
    )
    parser.add_argument("--price-folder", required=True)
    parser.add_argument("--external-folder", required=True)
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--mode", choices=("replication", "holdout"), default="replication")
    parser.add_argument("--replication-json")
    parser.add_argument("--expected-price-fingerprint")
    args = parser.parse_args(argv)
    if args.mode == "holdout":
        if not args.replication_json:
            raise SystemExit("--replication-json is required for holdout mode")
        if not args.expected_price_fingerprint:
            raise SystemExit("--expected-price-fingerprint is required for holdout mode")
        report = evaluate_holdout(
            args.price_folder,
            args.external_folder,
            args.replication_json,
            args.expected_price_fingerprint,
        )
    else:
        report = evaluate_replication(args.price_folder, args.external_folder)
    payload = asdict(report)
    base._write_json(args.json_out, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
