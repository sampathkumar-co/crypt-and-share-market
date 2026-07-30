from __future__ import annotations

import argparse
import json
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterator
from unittest.mock import patch

from tradebot.backtest import crypto_multiregime_4h as base
from tradebot.backtest import crypto_multiregime_4h_calendar_funding as calendar
from tradebot.backtest.research_gate import dataset_fingerprint
from tradebot.models import Candle, Market

SCHEMA_VERSION = "1.5.0"
FOUR_HOURS = timedelta(hours=4)
SEVEN_DAY_BUCKETS = 7 * 6
HISTORY_BUCKETS = 120 * 6

CONFIG = base.MultiRegimeConfig(
    max_positions=2,
    max_asset_weight=0.25,
    min_cash_reserve=0.50,
    target_volatility=0.25,
    max_drawdown=0.10,
)

VARIANTS = (
    base.MultiRegimeVariant("primary_consensus", ("primary",)),
    base.MultiRegimeVariant("without_cross_sectional_rank", ("without_cross",)),
    base.MultiRegimeVariant("without_price_recovery", ("without_recovery",)),
    base.MultiRegimeVariant("deep_extreme", ("deep_extreme",)),
    base.MultiRegimeVariant("legacy_funding_only", ("legacy",)),
)

_ORIGINAL_SIGNAL_CANDIDATES = base.signal_candidates
_ORIGINAL_POSITION_EXIT = base._position_exit
_STATE_CACHE: dict[tuple[int, str], dict[datetime, "FundingState"]] = {}


@dataclass(frozen=True)
class FundingState:
    current: float
    percentile_rank: float
    fifth: float
    fifteenth: float
    rolling_median: float


@dataclass
class FundingStateReport:
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
    config: dict[str, Any]
    variants: list[base.MultiRegimeSummary]
    primary_beats_legacy_periods: int
    positive_structural_diagnostics: int
    leave_one_asset_out_average_returns: dict[str, float]
    leave_one_asset_out_positive_count: int
    accepted: bool
    eligible_for_holdout: bool
    eligible_for_shadow_paper: bool
    eligible_for_forward_paper: bool
    reasons: list[str]
    paper_only: bool = True
    authorizes_real_trading: bool = False


def build_funding_states(series: dict[datetime, float]) -> dict[datetime, FundingState]:
    if not series:
        return {}
    bucketed = {
        base._four_hour_bucket(timestamp): float(value)
        for timestamp, value in series.items()
    }
    start = min(bucketed)
    end = max(bucketed)
    rolling: deque[float] = deque()
    rolling_total = 0.0
    seven_day_means: dict[datetime, float] = {}
    anchor = start
    while anchor <= end:
        value = bucketed.get(anchor, 0.0)
        rolling.append(value)
        rolling_total += value
        if len(rolling) > SEVEN_DAY_BUCKETS:
            rolling_total -= rolling.popleft()
        if len(rolling) == SEVEN_DAY_BUCKETS:
            seven_day_means[anchor] = rolling_total / SEVEN_DAY_BUCKETS
        anchor += FOUR_HOURS

    history: deque[float] = deque()
    output: dict[datetime, FundingState] = {}
    for anchor, current in seven_day_means.items():
        if len(history) == HISTORY_BUCKETS:
            values = list(history)
            output[anchor] = FundingState(
                current=current,
                percentile_rank=sum(value <= current for value in values) / len(values),
                fifth=base._percentile(values, 0.05),
                fifteenth=base._percentile(values, 0.15),
                rolling_median=median(values),
            )
        history.append(current)
        if len(history) > HISTORY_BUCKETS:
            history.popleft()
    return output


def funding_state(
    store: base.ExternalStore,
    symbol: str,
    as_of: datetime,
) -> FundingState | None:
    key = (id(store), symbol)
    states = _STATE_CACHE.get(key)
    if states is None:
        states = build_funding_states(store.funding.get(symbol, {}))
        _STATE_CACHE[key] = states
    return states.get(base._four_hour_bucket(as_of))


def _recovery_count(candles: list[Candle]) -> int:
    closes = [item.close for item in candles]
    return sum(
        (
            candles[-1].close > base._ema(closes, 12),
            candles[-1].close > candles[-2].high,
            base._simple_return(candles, 3) > 0,
        )
    )


def _candidate_metrics(
    symbol: str,
    candles: list[Candle],
    store: base.ExternalStore,
) -> tuple[FundingState, float, float, int] | None:
    if len(candles) < 180:
        return None
    state = funding_state(store, symbol, candles[-1].timestamp)
    if state is None:
        return None
    prior_high = max(item.close for item in candles[-121:-1])
    drawdown = candles[-1].close / prior_high - 1.0 if prior_high > 0 else 0.0
    atr = base._atr(candles, 20)
    if atr <= 0:
        return None
    return state, drawdown, atr, _recovery_count(candles)


def funding_state_candidates(
    prior: dict[str, list[Candle]],
    store: base.ExternalStore,
    enabled_sleeves: tuple[str, ...],
) -> dict[str, base.Candidate]:
    mode = enabled_sleeves[0] if enabled_sleeves else "primary"
    if mode == "legacy":
        return _ORIGINAL_SIGNAL_CANDIDATES(prior, store, ("funding",))

    metrics = {
        symbol: value
        for symbol, candles in prior.items()
        if (value := _candidate_metrics(symbol, candles, store)) is not None
    }
    ordered = sorted(
        metrics,
        key=lambda symbol: (
            metrics[symbol][0].percentile_rank,
            metrics[symbol][0].current,
            symbol,
        ),
    )
    cross_rank = {symbol: index + 1 for index, symbol in enumerate(ordered)}
    output: dict[str, base.Candidate] = {}
    for symbol, (state, drawdown, atr, recovery) in metrics.items():
        negative = state.current < 0
        rank = cross_rank[symbol]
        if mode == "primary":
            eligible = (
                negative
                and state.percentile_rank <= 0.15
                and rank <= 3
                and drawdown <= -0.12
                and recovery >= 2
            )
            threshold = 0.15
        elif mode == "without_cross":
            eligible = (
                negative
                and state.percentile_rank <= 0.15
                and drawdown <= -0.12
                and recovery >= 2
            )
            threshold = 0.15
        elif mode == "without_recovery":
            eligible = (
                negative
                and state.percentile_rank <= 0.15
                and rank <= 3
                and drawdown <= -0.12
            )
            threshold = 0.15
        elif mode == "deep_extreme":
            eligible = (
                negative
                and state.percentile_rank <= 0.05
                and drawdown <= -0.15
                and recovery >= 2
            )
            threshold = 0.05
        else:
            raise ValueError(f"Unknown v1.5 funding-state mode: {mode}")
        if not eligible:
            continue
        percentile_depth = max(0.0, threshold - state.percentile_rank) / max(threshold, 1e-9)
        cross_strength = (len(metrics) - rank + 1) / max(len(metrics), 1)
        recovery_strength = recovery / 3.0
        strength = percentile_depth + cross_strength + abs(drawdown) + recovery_strength
        output[symbol] = base.Candidate(symbol, "funding", strength, atr)
    return output


def funding_state_exit(
    position: base.PositionState,
    candles: list[Candle],
    store: base.ExternalStore,
    index: int,
    symbol: str,
) -> bool:
    if len(candles) < 180:
        return False
    close = candles[-1].close
    held = index - position.entry_index
    state = funding_state(store, symbol, candles[-1].timestamp)
    funding_recovered = state is not None and (
        state.current >= state.rolling_median or state.percentile_rank > 0.50
    )
    return (
        funding_recovered
        or close >= base._ema((item.close for item in candles), 72)
        or close < position.average_price - 2.25 * position.entry_atr
        or held >= 60
    )


@contextmanager
def funding_state_model() -> Iterator[None]:
    with patch.object(base, "signal_candidates", funding_state_candidates), patch.object(
        base, "_position_exit", funding_state_exit
    ):
        yield


def _variant_period(
    histories: dict[str, list[Candle]],
    store: base.ExternalStore,
    variant: base.MultiRegimeVariant,
    period: int,
    config: base.MultiRegimeConfig,
    mode: str,
) -> base.MultiRegimePeriod:
    if variant.name == "legacy_funding_only":
        legacy = base.MultiRegimeVariant("legacy_funding_only", ("funding",))
        with calendar.calendar_funding_model():
            return base._simulate_period(histories, store, legacy, period, config, mode)
    with funding_state_model():
        return base._simulate_period(histories, store, variant, period, config, mode)


def _variant_summary(
    histories: dict[str, list[Candle]],
    store: base.ExternalStore,
    variant: base.MultiRegimeVariant,
    config: base.MultiRegimeConfig,
    mode: str,
) -> base.MultiRegimeSummary:
    count = config.discovery_periods if mode == "discovery" else config.holdout_periods
    periods = [
        _variant_period(histories, store, variant, period, config, mode)
        for period in range(1, count + 1)
    ]
    return base._summarize(variant.name, periods)


def _report(
    histories: dict[str, list[Candle]],
    store: base.ExternalStore,
    summaries: list[base.MultiRegimeSummary],
    config: base.MultiRegimeConfig,
    mode: str,
    reasons: list[str],
    primary_beats_legacy: int,
    positive_diagnostics: int,
    leave_one_out: dict[str, float],
) -> FundingStateReport:
    bounds = base._date_boundaries(histories, config)
    accepted = not reasons
    return FundingStateReport(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        mode=mode,
        market=Market.CRYPTO.value,
        symbols=sorted(histories),
        price_dataset_fingerprint=dataset_fingerprint(histories),
        external_manifest_fingerprint=store.manifest_fingerprint,
        dataset_start=histories[next(iter(histories))][0].timestamp.isoformat(),
        dataset_end=histories[next(iter(histories))][-1].timestamp.isoformat(),
        config=asdict(config),
        variants=summaries,
        primary_beats_legacy_periods=primary_beats_legacy,
        positive_structural_diagnostics=positive_diagnostics,
        leave_one_asset_out_average_returns=leave_one_out,
        leave_one_asset_out_positive_count=sum(value > 0 for value in leave_one_out.values()),
        accepted=accepted,
        eligible_for_holdout=accepted if mode == "discovery" else False,
        eligible_for_shadow_paper=accepted if mode == "holdout" else False,
        eligible_for_forward_paper=False,
        reasons=reasons,
        **bounds,
    )


def evaluate_discovery(
    price_folder: str | Path,
    external_folder: str | Path,
    market: Market = Market.CRYPTO,
    config: base.MultiRegimeConfig | None = None,
) -> FundingStateReport:
    if market != Market.CRYPTO:
        raise ValueError("v1.5 is frozen to crypto")
    config = config or CONFIG
    histories = base.load_exact_histories(price_folder, config)
    store = base.load_external_store(external_folder)
    summaries = [
        _variant_summary(histories, store, variant, config, "discovery")
        for variant in VARIANTS
    ]
    summaries.extend(
        (
            base._pseudo_summary(histories, config, "discovery", "cash"),
            base._pseudo_summary(histories, config, "discovery", "equal_weight_buy_hold"),
        )
    )
    by_name = {item.variant: item for item in summaries}
    primary = by_name["primary_consensus"]
    legacy = by_name["legacy_funding_only"]
    equal_weight = by_name["equal_weight_buy_hold"]
    beats_legacy = sum(
        left.net_return > right.net_return
        for left, right in zip(primary.periods, legacy.periods)
    )
    diagnostic_names = (
        "without_cross_sectional_rank",
        "without_price_recovery",
        "deep_extreme",
    )
    positive_diagnostics = sum(by_name[name].average_return > 0 for name in diagnostic_names)
    leave_one_out: dict[str, float] = {}
    for omitted in base.REQUIRED_SYMBOLS:
        subset = {
            symbol: candles
            for symbol, candles in histories.items()
            if symbol != omitted
        }
        leave_one_out[omitted] = _variant_summary(
            subset, store, VARIANTS[0], config, "discovery"
        ).average_return

    reasons: list[str] = []
    if len(primary.periods) != config.discovery_periods:
        reasons.append("incomplete_discovery_periods")
    if primary.active_periods < 5:
        reasons.append("too_few_active_periods")
    if primary.positive_periods < 4:
        reasons.append("too_few_profitable_periods")
    if primary.average_return <= 0:
        reasons.append("average_return_not_positive")
    if primary.median_return <= 0:
        reasons.append("median_return_not_positive")
    if primary.compounded_return <= 0:
        reasons.append("compounded_return_not_positive")
    if primary.average_stressed_return <= 0:
        reasons.append("extra_cost_stressed_return_not_positive")
    if primary.first_half_average <= 0:
        reasons.append("first_half_not_positive")
    if primary.second_half_average <= 0:
        reasons.append("second_half_not_positive")
    if primary.average_return <= equal_weight.average_return:
        reasons.append("does_not_beat_equal_weight_average")
    if primary.average_return <= legacy.average_return:
        reasons.append("does_not_beat_legacy_funding_average")
    if beats_legacy < 4:
        reasons.append("does_not_beat_legacy_funding_often_enough")
    if primary.worst_drawdown > config.max_drawdown:
        reasons.append("drawdown_too_high")
    if len(primary.selected_symbols) < 5:
        reasons.append("too_few_distinct_assets")
    if primary.max_asset_notional_fraction > 0.35:
        reasons.append("asset_notional_concentration_too_high")
    if positive_diagnostics < 2:
        reasons.append("structural_diagnostics_not_robust")
    if sum(value > 0 for value in leave_one_out.values()) < 6:
        reasons.append("leave_one_asset_out_not_robust")
    return _report(
        histories,
        store,
        summaries,
        config,
        "discovery",
        reasons,
        beats_legacy,
        positive_diagnostics,
        leave_one_out,
    )


def evaluate_holdout(
    price_folder: str | Path,
    external_folder: str | Path,
    discovery_json: str | Path,
    market: Market = Market.CRYPTO,
    config: base.MultiRegimeConfig | None = None,
) -> FundingStateReport:
    if market != Market.CRYPTO:
        raise ValueError("v1.5 is frozen to crypto")
    config = config or CONFIG
    discovery = json.loads(Path(discovery_json).read_text(encoding="utf-8"))
    if discovery.get("accepted") is not True or discovery.get("eligible_for_holdout") is not True:
        raise ValueError("v1.5 holdout is locked because discovery did not pass")
    histories = base.load_exact_histories(price_folder, config)
    store = base.load_external_store(external_folder)
    if discovery.get("price_dataset_fingerprint") != dataset_fingerprint(histories):
        raise ValueError("v1.5 holdout price fingerprint changed")
    if discovery.get("external_manifest_fingerprint") != store.manifest_fingerprint:
        raise ValueError("v1.5 holdout external fingerprint changed")
    summaries = [
        _variant_summary(histories, store, VARIANTS[0], config, "holdout"),
        _variant_summary(histories, store, VARIANTS[-1], config, "holdout"),
        base._pseudo_summary(histories, config, "holdout", "cash"),
        base._pseudo_summary(histories, config, "holdout", "equal_weight_buy_hold"),
    ]
    by_name = {item.variant: item for item in summaries}
    primary = by_name["primary_consensus"]
    legacy = by_name["legacy_funding_only"]
    equal_weight = by_name["equal_weight_buy_hold"]
    beats_legacy = sum(
        left.net_return > right.net_return
        for left, right in zip(primary.periods, legacy.periods)
    )
    reasons: list[str] = []
    if primary.active_periods < 2:
        reasons.append("too_few_active_holdout_periods")
    if primary.positive_periods < 2:
        reasons.append("too_few_profitable_holdout_periods")
    if primary.average_return <= 0:
        reasons.append("holdout_average_not_positive")
    if primary.compounded_return <= 0:
        reasons.append("holdout_compounded_not_positive")
    if primary.average_stressed_return <= 0:
        reasons.append("holdout_stressed_not_positive")
    if primary.average_return <= legacy.average_return:
        reasons.append("holdout_does_not_beat_legacy_funding")
    if primary.average_return <= equal_weight.average_return:
        reasons.append("holdout_does_not_beat_equal_weight")
    if primary.worst_drawdown > config.max_drawdown:
        reasons.append("holdout_drawdown_too_high")
    if len(primary.selected_symbols) < 3:
        reasons.append("holdout_too_few_assets")
    if primary.max_asset_notional_fraction > 0.45:
        reasons.append("holdout_asset_concentration_too_high")
    return _report(
        histories,
        store,
        summaries,
        config,
        "holdout",
        reasons,
        beats_legacy,
        0,
        {},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate frozen v1.5 funding-state crypto research"
    )
    parser.add_argument("--price-folder", required=True)
    parser.add_argument("--external-folder", required=True)
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--mode", choices=("discovery", "holdout"), default="discovery")
    parser.add_argument("--discovery-json")
    args = parser.parse_args(argv)
    if args.mode == "holdout":
        if not args.discovery_json:
            raise SystemExit("--discovery-json is required for holdout mode")
        report = evaluate_holdout(
            args.price_folder,
            args.external_folder,
            args.discovery_json,
        )
    else:
        report = evaluate_discovery(args.price_folder, args.external_folder)
    payload = asdict(report)
    base._write_json(args.json_out, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
