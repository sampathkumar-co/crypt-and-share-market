from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any
from unittest.mock import patch

from tradebot.backtest import crypto_discrete_factor_veto as v12
from tradebot.backtest import crypto_multifactor as base
from tradebot.backtest.research_gate import dataset_fingerprint
from tradebot.data.crypto_external_factors import (
    COINMETRICS_ASSETS,
    ExternalFactorDataError,
    sha256_file,
    verify_external_manifest,
)
from tradebot.models import Candle, Market

SCHEMA_VERSION = "1.3"
EXPECTED_PRICE_FINGERPRINT = "ef9a33096cd37c74626bf0f0dbee8c740dd09cafb0f49b336c689d3cd1ca21e3"
EXPECTED_FULL_START = "2020-12-19T00:00:00"
EXPECTED_FULL_END = "2026-07-30T00:00:00"
EXPECTED_HOLDOUT_START = date(2025, 11, 23)
EXPECTED_TEST_END = date(2026, 5, 21)
EXPECTED_EMBARGO_START = date(2026, 5, 22)
EXPECTED_HOLDOUT_END = date(2026, 7, 30)

VARIANTS = (
    base.MultiFactorVariant("primary_multisource", "multisource", 14, 2, 0.20, 0.40, 0.30, 0.0),
    base.MultiFactorVariant("without_stablecoin", "drop_stablecoin", 14, 2, 0.20, 0.40, 0.30, 0.0),
    base.MultiFactorVariant("without_onchain", "drop_onchain", 14, 2, 0.20, 0.40, 0.30, 0.0),
    base.MultiFactorVariant("without_derivatives", "drop_derivatives", 14, 2, 0.20, 0.40, 0.30, 0.0),
    base.MultiFactorVariant("without_macro", "drop_macro", 14, 2, 0.20, 0.40, 0.30, 0.0),
    base.MultiFactorVariant("raw_simple_trend", "raw_simple", 14, 2, 0.20, 0.40, 0.30, 0.0),
)


@dataclass(frozen=True)
class MultiSourceHoldoutConfig:
    full_history_bars: int = 2050
    discovery_bars: int = 1800
    holdout_bars: int = 250
    price_warmup_bars: int = 200
    test_periods: int = 3
    test_bars: int = 60
    embargo_bars: int = 70
    min_trade_weight: float = 0.04
    extra_cost_per_turnover: float = 0.0015
    starting_cash: float = 100000.0
    max_portfolio_drawdown: float = 0.15
    min_active_periods: int = 2
    min_profitable_periods: int = 2
    min_selected_assets: int = 3
    min_positive_source_ablations: int = 3

    def __post_init__(self) -> None:
        if self.discovery_bars + self.holdout_bars != self.full_history_bars:
            raise ValueError("discovery and holdout must consume exactly 2050 price bars")
        if self.test_periods * self.test_bars + self.embargo_bars != self.holdout_bars:
            raise ValueError("three tests and embargo must consume exactly 250 holdout bars")
        if self.starting_cash <= 0:
            raise ValueError("starting_cash must be positive")


@dataclass
class MultiSourceHoldoutReport:
    schema_version: str
    generated_at: str
    market: str
    symbols: list[str]
    price_dataset_fingerprint: str
    external_manifest_fingerprint: str
    price_start: str
    price_end: str
    holdout_test_start: str
    holdout_test_end: str
    embargo_start: str
    embargo_end: str
    config: dict[str, Any]
    variants: list[base.MultiFactorSummary]
    primary_first_two_average: float
    primary_last_two_average: float
    primary_beats_raw_fraction: float
    primary_unique_selected_assets: list[str]
    positive_source_ablations: int
    onchain_covered_assets: list[str]
    external_sources: list[dict[str, Any]]
    accepted: bool
    eligible_for_shadow_paper: bool
    eligible_for_forward_paper: bool
    reasons: list[str]
    paper_only: bool = True
    authorizes_real_trading: bool = False


@dataclass(frozen=True)
class ExternalFactorStore:
    stablecoin: dict[str, dict[date, float]]
    onchain: dict[str, dict[str, dict[date, float]]]
    funding: dict[str, dict[date, float]]
    macro: dict[str, dict[date, float]]
    manifest: dict[str, Any]
    manifest_fingerprint: str


def _base_config(config: MultiSourceHoldoutConfig) -> base.CryptoMultiFactorConfig:
    return base.CryptoMultiFactorConfig(
        history_bars=config.price_warmup_bars + config.holdout_bars,
        warmup_bars=config.price_warmup_bars,
        early_periods=config.test_periods,
        late_periods=0,
        test_bars=config.test_bars,
        middle_embargo_bars=0,
        final_embargo_bars=config.embargo_bars,
        min_trade_weight=config.min_trade_weight,
        extra_cost_per_turnover=config.extra_cost_per_turnover,
        starting_cash=config.starting_cash,
    )


def _read_numeric_csv(path: Path, date_column: str, value_columns: tuple[str, ...]) -> dict[str, dict[date, float]]:
    result = {column: {} for column in value_columns}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or date_column not in reader.fieldnames:
            raise ExternalFactorDataError(f"Missing {date_column} in {path}")
        for row in reader:
            try:
                row_date = date.fromisoformat(row[date_column][:10])
            except (KeyError, ValueError):
                continue
            for column in value_columns:
                raw = row.get(column, "").strip()
                if raw not in {"", ".", "NA"}:
                    try:
                        result[column][row_date] = float(raw)
                    except ValueError:
                        continue
    return result


def _read_funding_daily(path: Path) -> dict[date, float]:
    grouped: dict[date, list[float]] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                row_date = datetime.fromisoformat(row["timestamp"]).date()
                value = float(row["funding_rate"])
            except (KeyError, ValueError):
                continue
            grouped.setdefault(row_date, []).append(value)
    return {row_date: mean(values) for row_date, values in grouped.items() if values}


def _load_external_store(root: str | Path) -> ExternalFactorStore:
    base_path = Path(root)
    manifest = verify_external_manifest(base_path)
    stablecoin: dict[str, dict[date, float]] = {}
    for asset in ("usdt", "usdc"):
        path = base_path / "coinmetrics" / f"{asset}.csv"
        stablecoin[asset] = _read_numeric_csv(path, "date", ("CapMrktCurUSD",))["CapMrktCurUSD"]

    onchain: dict[str, dict[str, dict[date, float]]] = {}
    for symbol, asset in COINMETRICS_ASSETS.items():
        path = base_path / "coinmetrics" / f"{asset}.csv"
        if path.is_file():
            values = _read_numeric_csv(path, "date", ("AdrActCnt", "TxCnt"))
            if values["AdrActCnt"] and values["TxCnt"]:
                onchain[symbol] = values

    funding = {
        symbol: _read_funding_daily(base_path / "bybit" / f"{symbol}.csv")
        for symbol in v12.REQUIRED_SYMBOLS
    }
    macro = {
        series: _read_numeric_csv(base_path / "fred" / f"{series}.csv", "date", ("value",))["value"]
        for series in ("VIXCLS", "DTWEXBGS", "DGS10")
    }
    return ExternalFactorStore(
        stablecoin=stablecoin,
        onchain=onchain,
        funding=funding,
        macro=macro,
        manifest=manifest,
        manifest_fingerprint=sha256_file(base_path / "manifest.json"),
    )


def _sorted_values(series: dict[date, float], as_of: date) -> list[tuple[date, float]]:
    return sorted((row_date, value) for row_date, value in series.items() if row_date <= as_of)


def _last_on_or_before(series: dict[date, float], as_of: date) -> float | None:
    values = _sorted_values(series, as_of)
    return values[-1][1] if values else None


def _stablecoin_supportive(store: ExternalFactorStore, as_of: date) -> bool:
    def total(on_date: date) -> float | None:
        left = _last_on_or_before(store.stablecoin["usdt"], on_date)
        right = _last_on_or_before(store.stablecoin["usdc"], on_date)
        if left is None or right is None:
            return None
        return left + right

    current = total(as_of)
    prior_30 = total(as_of - timedelta(days=30))
    prior_90 = total(as_of - timedelta(days=90))
    if current is None or prior_30 is None or prior_90 is None or min(prior_30, prior_90) <= 0:
        return False
    return current > prior_30 and current > prior_90


def _onchain_supportive(store: ExternalFactorStore, selected: list[str], as_of: date) -> bool:
    covered = 0
    improved = 0
    for symbol in selected:
        metrics = store.onchain.get(symbol)
        if not metrics:
            continue
        metric_results: list[bool] = []
        valid = True
        for metric in ("AdrActCnt", "TxCnt"):
            values = _sorted_values(metrics[metric], as_of)
            if len(values) < 104:
                valid = False
                break
            recent = [value for _, value in values[-14:]]
            preceding = [value for _, value in values[-104:-14]]
            metric_results.append(mean(recent) > median(preceding))
        if valid:
            covered += 1
            improved += int(all(metric_results))
    return covered > 0 and improved / covered >= 0.50


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(math.ceil(fraction * (len(ordered) - 1)))
    return ordered[index]


def _funding_supportive(store: ExternalFactorStore, selected: list[str], as_of: date) -> bool:
    daily_medians: dict[date, float] = {}
    start = as_of - timedelta(days=110)
    cursor = start
    while cursor <= as_of:
        values = [store.funding[symbol][cursor] for symbol in selected if cursor in store.funding.get(symbol, {})]
        if values:
            daily_medians[cursor] = median(values)
        cursor += timedelta(days=1)
    recent = [value for row_date, value in daily_medians.items() if as_of - timedelta(days=6) <= row_date <= as_of]
    history = [
        value
        for row_date, value in daily_medians.items()
        if as_of - timedelta(days=97) <= row_date <= as_of - timedelta(days=7)
    ]
    if len(recent) < 4 or len(history) < 45:
        return False
    return mean(recent) <= _percentile(history, 0.75)


def _observation_change(series: dict[date, float], as_of: date, observations: int) -> tuple[float, float] | None:
    values = _sorted_values(series, as_of)
    if len(values) <= observations:
        return None
    return values[-1][1], values[-1 - observations][1]


def _macro_supportive(store: ExternalFactorStore, as_of: date) -> bool:
    supportive = 0
    vix = _observation_change(store.macro["VIXCLS"], as_of, 20)
    if vix is not None and vix[1] > 0:
        supportive += int(vix[0] <= 30.0 and (vix[0] / vix[1] - 1.0) <= 0.25)
    dollar = _observation_change(store.macro["DTWEXBGS"], as_of, 20)
    if dollar is not None and dollar[1] > 0:
        supportive += int((dollar[0] / dollar[1] - 1.0) < 0.02)
    ten_year = _observation_change(store.macro["DGS10"], as_of, 20)
    if ten_year is not None:
        supportive += int((ten_year[0] - ten_year[1]) < 0.50)
    return supportive >= 2


def _source_support(
    store: ExternalFactorStore,
    selected: list[str],
    as_of: date,
) -> dict[str, bool]:
    return {
        "stablecoin": _stablecoin_supportive(store, as_of),
        "onchain": _onchain_supportive(store, selected, as_of),
        "derivatives": _funding_supportive(store, selected, as_of),
        "macro": _macro_supportive(store, as_of),
    }


def _enabled_sources(factor_set: str) -> tuple[str, ...]:
    all_sources = ("stablecoin", "onchain", "derivatives", "macro")
    if factor_set == "multisource":
        return all_sources
    if factor_set.startswith("drop_"):
        removed = factor_set.removeprefix("drop_")
        return tuple(source for source in all_sources if source != removed)
    return ()


def _support_scale(supportive: int, total: int) -> float:
    if total <= 0:
        return 0.0
    fraction = supportive / total
    if fraction < 0.50:
        return 0.0
    if fraction < 0.75:
        return 0.50
    if fraction < 1.0:
        return 0.75
    return 1.0


def _scale_weights(weights: dict[str, float], multiplier: float) -> dict[str, float]:
    multiplier = max(0.0, min(1.0, multiplier))
    return {symbol: weight * multiplier for symbol, weight in weights.items() if weight * multiplier > 1e-12}


def _target_weights(
    prior: dict[str, list[Candle]],
    variant: base.MultiFactorVariant,
    drawdown_multiplier: float,
    store: ExternalFactorStore,
) -> tuple[dict[str, float], str]:
    raw_variant = base.MultiFactorVariant(
        variant.name,
        "simple",
        variant.rebalance_bars,
        variant.top_n,
        variant.min_cash_reserve,
        variant.max_asset_weight,
        variant.target_volatility,
        0.0,
    )
    raw_weights, raw_regime = base._simple_trend_target(prior, raw_variant)
    if variant.factor_set == "raw_simple":
        return raw_weights, raw_regime
    if not raw_weights:
        return {}, "trend_cash"
    selected = list(raw_weights)
    # External daily observations are conservatively lagged by one extra calendar day.
    # This avoids assuming a value stamped with the prior date was published before
    # the next Coinbase 00:00 UTC execution open.
    as_of = prior[next(iter(prior))][-1].timestamp.date() - timedelta(days=1)
    support = _source_support(store, selected, as_of)
    enabled = _enabled_sources(variant.factor_set)
    count = sum(support[source] for source in enabled)
    scale = _support_scale(count, len(enabled)) * max(0.0, min(1.0, drawdown_multiplier))
    return _scale_weights(raw_weights, scale), f"support_{count}_of_{len(enabled)}"


def _load_exact_prices(folder: str | Path, config: MultiSourceHoldoutConfig) -> tuple[dict[str, list[Candle]], dict[str, list[Candle]]]:
    full, discovery, holdout = v12._aligned_histories(
        folder,
        v12.DiscreteFactorVetoConfig(
            history_bars=config.full_history_bars,
            discovery_bars=config.discovery_bars,
            holdout_bars=config.holdout_bars,
        ),
    )
    fingerprint = dataset_fingerprint(full)
    if fingerprint != EXPECTED_PRICE_FINGERPRINT:
        raise ValueError("Price history does not match the sealed v1.2 dataset fingerprint")
    first = full[next(iter(full))][0].timestamp.isoformat()
    last = full[next(iter(full))][-1].timestamp.isoformat()
    if first != EXPECTED_FULL_START or last != EXPECTED_FULL_END:
        raise ValueError("Price history does not match the sealed v1.2 date interval")
    if holdout[next(iter(holdout))][0].timestamp.date() != EXPECTED_HOLDOUT_START:
        raise ValueError("Unexpected holdout start")
    evaluation = {
        symbol: discovery[symbol][-config.price_warmup_bars :] + holdout[symbol]
        for symbol in full
    }
    return full, evaluation


def _validate_external_coverage(store: ExternalFactorStore) -> list[str]:
    start = date(2025, 5, 27)
    end = EXPECTED_TEST_END
    for asset in ("usdt", "usdc"):
        dates = sorted(store.stablecoin[asset])
        if not dates or dates[0] > start or dates[-1] < end:
            raise ExternalFactorDataError(f"Incomplete {asset} liquidity coverage")
    for symbol, series in store.funding.items():
        dates = sorted(series)
        if not dates or dates[0] > start or dates[-1] < end:
            raise ExternalFactorDataError(f"Incomplete funding coverage for {symbol}")
    for series_name, series in store.macro.items():
        dates = sorted(series)
        if not dates or dates[0] > start or dates[-1] < end:
            raise ExternalFactorDataError(f"Incomplete macro coverage for {series_name}")
    covered: list[str] = []
    for symbol, metrics in store.onchain.items():
        dates = sorted(set(metrics["AdrActCnt"]) & set(metrics["TxCnt"]))
        if dates and dates[0] <= start and dates[-1] >= end:
            covered.append(symbol)
    if len(covered) < 5:
        raise ExternalFactorDataError("Fewer than five assets have complete on-chain coverage")
    return sorted(covered)


def _variant_result(
    histories: dict[str, list[Candle]],
    variant: base.MultiFactorVariant,
    config: MultiSourceHoldoutConfig,
    store: ExternalFactorStore,
) -> base.MultiFactorSummary:
    base_config = _base_config(config)

    def target(
        prior: dict[str, list[Candle]],
        chosen: base.MultiFactorVariant,
        drawdown_multiplier: float,
    ) -> tuple[dict[str, float], str]:
        return _target_weights(prior, chosen, drawdown_multiplier, store)

    with patch.object(base, "_target_weights", target):
        periods = [
            base._simulate_period(histories, period, variant, base_config)
            for period in range(1, config.test_periods + 1)
        ]
    return base._summarize(variant, periods)


def evaluate_multisource_holdout(
    price_folder: str | Path,
    external_folder: str | Path,
    market: Market = Market.CRYPTO,
    config: MultiSourceHoldoutConfig | None = None,
) -> MultiSourceHoldoutReport:
    if market != Market.CRYPTO:
        raise ValueError("v1.3 is frozen to crypto")
    config = config or MultiSourceHoldoutConfig()
    full, evaluation = _load_exact_prices(price_folder, config)
    store = _load_external_store(external_folder)
    covered = _validate_external_coverage(store)

    summaries = [_variant_result(evaluation, variant, config, store) for variant in VARIANTS]
    by_name = {summary.variant: summary for summary in summaries}
    primary = by_name["primary_multisource"]
    raw = by_name["raw_simple_trend"]
    first_two = mean(period.net_return for period in primary.periods[:2])
    last_two = mean(period.net_return for period in primary.periods[1:])
    beats_raw_fraction = sum(
        left.net_return > right.net_return
        for left, right in zip(primary.periods, raw.periods)
    ) / len(primary.periods)
    selected = sorted({symbol for period in primary.periods for symbol in period.selected_symbols})
    ablation_names = (
        "without_stablecoin",
        "without_onchain",
        "without_derivatives",
        "without_macro",
    )
    positive_ablations = sum(by_name[name].average_return > 0 for name in ablation_names)

    reasons: list[str] = []
    if len(primary.periods) != config.test_periods:
        reasons.append("incomplete_holdout_periods")
    if primary.average_return <= 0:
        reasons.append("average_holdout_return_not_positive")
    if primary.compounded_return <= 0:
        reasons.append("compounded_holdout_return_not_positive")
    if primary.average_stressed_return <= 0:
        reasons.append("extra_cost_stressed_return_not_positive")
    if primary.positive_periods < config.min_profitable_periods:
        reasons.append("too_few_profitable_holdout_periods")
    if first_two <= 0:
        reasons.append("first_two_period_average_not_positive")
    if last_two <= 0:
        reasons.append("last_two_period_average_not_positive")
    if primary.average_return <= raw.average_return:
        reasons.append("does_not_beat_raw_trend_average")
    if beats_raw_fraction < 2 / 3:
        reasons.append("does_not_beat_raw_trend_often_enough")
    if primary.worst_drawdown > config.max_portfolio_drawdown:
        reasons.append("drawdown_too_high")
    if primary.active_periods < config.min_active_periods:
        reasons.append("too_few_active_holdout_periods")
    if len(selected) < config.min_selected_assets:
        reasons.append("too_few_distinct_selected_assets")
    if positive_ablations < config.min_positive_source_ablations:
        reasons.append("source_family_ablations_not_robust")

    accepted = not reasons
    base_config = _base_config(config)
    period_1_start, _ = base._period_bounds(1, base_config)
    _, period_3_end = base._period_bounds(3, base_config)
    test_start = evaluation[next(iter(evaluation))][period_1_start].timestamp
    test_end = evaluation[next(iter(evaluation))][period_3_end].timestamp
    embargo_start = evaluation[next(iter(evaluation))][period_3_end + 1].timestamp
    embargo_end = evaluation[next(iter(evaluation))][-1].timestamp
    if (
        test_start.date() != EXPECTED_HOLDOUT_START
        or test_end.date() != EXPECTED_TEST_END
        or embargo_start.date() != EXPECTED_EMBARGO_START
        or embargo_end.date() != EXPECTED_HOLDOUT_END
    ):
        raise ValueError("Holdout or embargo boundaries changed")

    return MultiSourceHoldoutReport(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        market=market.value,
        symbols=sorted(full),
        price_dataset_fingerprint=dataset_fingerprint(full),
        external_manifest_fingerprint=store.manifest_fingerprint,
        price_start=EXPECTED_FULL_START,
        price_end=EXPECTED_FULL_END,
        holdout_test_start=test_start.isoformat(),
        holdout_test_end=test_end.isoformat(),
        embargo_start=embargo_start.isoformat(),
        embargo_end=embargo_end.isoformat(),
        config=asdict(config),
        variants=summaries,
        primary_first_two_average=first_two,
        primary_last_two_average=last_two,
        primary_beats_raw_fraction=beats_raw_fraction,
        primary_unique_selected_assets=selected,
        positive_source_ablations=positive_ablations,
        onchain_covered_assets=covered,
        external_sources=store.manifest.get("files", []),
        accepted=accepted,
        eligible_for_shadow_paper=accepted,
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
    parser = argparse.ArgumentParser(description="Evaluate the frozen v1.3 crypto multi-source holdout")
    parser.add_argument("--price-folder", required=True)
    parser.add_argument("--external-folder", required=True)
    parser.add_argument("--market", choices=[market.value for market in Market], default=Market.CRYPTO.value)
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args(argv)
    report = evaluate_multisource_holdout(
        args.price_folder,
        args.external_folder,
        Market(args.market),
    )
    payload = asdict(report)
    _write_json(args.json_out, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
