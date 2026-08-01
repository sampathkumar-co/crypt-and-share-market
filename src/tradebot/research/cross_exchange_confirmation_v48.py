from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import numpy as np

from tradebot.research import distributional_utility_v43 as v43
from tradebot.research import historical_coinbase_replication_v32 as cb32
from tradebot.research import walk_forward_selective_veto_v46 as v46
from tradebot.research import yield_bearing_cash_v44 as v44
from tradebot.research import yield_bearing_cash_v44_reproduce as v44_reproduce
from tradebot.research.regime_ranking_v42 import (
    ASSETS,
    STANDARD_ONE_WAY_COST,
    STRESS_ONE_WAY_COST,
    Dataset,
    build_dataset,
    file_sha256,
    state_arrays,
)
from tradebot.research.regime_ranking_v42_sources import (
    canonical_json,
    load_all_sources,
    utc_iso,
)

SCHEMA_VERSION = "4.8-cross-exchange-confirmation"
PROTOCOL_PATH = Path("research/V48_CROSS_EXCHANGE_CONFIRMATION_PROTOCOL.md")
CONTRACT_PATH = Path(
    "research/V481_CROSS_EXCHANGE_CONFIRMATION_IMPLEMENTATION_CONTRACT.md"
)
COINBASE_BASE_URL = "https://api.exchange.coinbase.com"
COINBASE_PRODUCTS = {"BTC": "BTC-USD", "ETH": "ETH-USD"}
COINBASE_START = datetime(2021, 1, 1, tzinfo=timezone.utc)
COINBASE_END = datetime(2026, 6, 30, tzinfo=timezone.utc)
COINBASE_GRANULARITY = 86_400
COINBASE_CHUNK_DAYS = 250
ACTIVE_FAMILIES = ("price", "liquidity", "combined")
ALL_FAMILIES = ("baseline", *ACTIVE_FAMILIES)


class CrossExchangeConfirmationV48Error(RuntimeError):
    pass


@dataclass(frozen=True)
class CoinbaseHistory:
    bars: dict[str, dict[datetime, Any]]
    source: dict[str, Any]


def request_ranges(
    start: datetime = COINBASE_START,
    end: datetime = COINBASE_END,
) -> list[tuple[datetime, datetime]]:
    if start > end:
        raise CrossExchangeConfirmationV48Error(
            "Coinbase request start exceeds end"
        )
    result: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor <= end:
        finish = min(
            end,
            cursor + timedelta(days=COINBASE_CHUNK_DAYS - 1),
        )
        result.append((cursor, finish))
        cursor = finish + timedelta(days=1)
    return result


def candle_url(
    product: str,
    start: datetime,
    end: datetime,
) -> str:
    query = urlencode({
        "granularity": str(COINBASE_GRANULARITY),
        "start": utc_iso(start),
        "end": utc_iso(end + timedelta(days=1)),
    })
    return f"{COINBASE_BASE_URL}/products/{product}/candles?{query}"


def required_dates(
    start: datetime = COINBASE_START,
    end: datetime = COINBASE_END,
) -> list[datetime]:
    result: list[datetime] = []
    cursor = start
    while cursor <= end:
        result.append(cursor)
        cursor += timedelta(days=1)
    return result


def download_coinbase_history(
    *,
    downloader: Callable[..., tuple[bytes, str]] = cb32._download_json,
    sleeper: Callable[[float], None] = time.sleep,
) -> CoinbaseHistory:
    bars: dict[str, dict[datetime, Any]] = {
        asset: {} for asset in COINBASE_PRODUCTS
    }
    inventory: list[dict[str, Any]] = []
    for asset, product in COINBASE_PRODUCTS.items():
        for index, (start, end) in enumerate(request_ranges()):
            url = candle_url(product, start, end)
            content, digest = downloader(url)
            parsed = cb32._parse_coinbase_candles(
                content,
                asset=asset,
                requested_start=start,
                requested_end=end,
            )
            for stamp, bar in parsed.items():
                prior = bars[asset].get(stamp)
                if prior is not None and prior != bar:
                    raise CrossExchangeConfirmationV48Error(
                        f"Coinbase {asset} cross-chunk conflict "
                        f"{stamp.date()}"
                    )
                bars[asset][stamp] = bar
            inventory.append({
                "key": f"coinbase:{asset}:{index:02d}",
                "provider": "coinbase-exchange-public-rest",
                "product": product,
                "start": start.date().isoformat(),
                "end": end.date().isoformat(),
                "url": url,
                "raw_sha256": digest,
                "rows": len(parsed),
            })
            sleeper(0.10)

    expected = required_dates()
    for asset in COINBASE_PRODUCTS:
        missing = [stamp for stamp in expected if stamp not in bars[asset]]
        if missing:
            raise CrossExchangeConfirmationV48Error(
                f"Coinbase {asset} missing {len(missing)} required candles; "
                f"first={missing[0].date()}"
            )
    inventory.sort(key=lambda value: value["key"])
    return CoinbaseHistory(
        bars=bars,
        source={
            "schema_version": SCHEMA_VERSION + "-coinbase-source",
            "provider": "coinbase-exchange-public-rest",
            "products": dict(COINBASE_PRODUCTS),
            "source_start": COINBASE_START.date().isoformat(),
            "source_end": COINBASE_END.date().isoformat(),
            "request_count": len(inventory),
            "inventory": inventory,
            "inventory_sha256": hashlib.sha256(
                canonical_json(inventory).encode("utf-8")
            ).hexdigest(),
            "complete_dates": {
                asset: len(bars[asset])
                for asset in sorted(COINBASE_PRODUCTS)
            },
        },
    )


PRICE_FEATURES = (
    "cb_btc_return_1",
    "cb_btc_return_7",
    "cb_btc_return_30",
    "cb_eth_return_1",
    "cb_eth_return_7",
    "cb_eth_return_30",
    "cb_market_return_7",
    "cb_market_return_30",
    "cb_minus_binance_btc_return_1",
    "cb_minus_binance_btc_return_7",
    "cb_minus_binance_btc_return_30",
    "cb_minus_binance_eth_return_1",
    "cb_minus_binance_eth_return_7",
    "cb_minus_binance_eth_return_30",
    "cb_btc_usd_usdt_premium",
    "cb_eth_usd_usdt_premium",
    "cb_btc_premium_change_1",
    "cb_btc_premium_change_7",
    "cb_eth_premium_change_1",
    "cb_eth_premium_change_7",
    "cross_exchange_momentum_agreement_7",
    "cross_exchange_momentum_agreement_30",
)
LIQUIDITY_FEATURES = (
    "cb_btc_volume_change_1",
    "cb_btc_volume_change_7",
    "cb_btc_volume_change_30",
    "cb_eth_volume_change_1",
    "cb_eth_volume_change_7",
    "cb_eth_volume_change_30",
    "cb_btc_volume_share",
    "cb_eth_volume_share",
    "cb_btc_volume_share_change_1",
    "cb_btc_volume_share_change_7",
    "cb_eth_volume_share_change_1",
    "cb_eth_volume_share_change_7",
    "cb_aggregate_volume_share",
    "cb_aggregate_volume_share_change_1",
    "cb_aggregate_volume_share_change_7",
    "cb_liquidity_breadth_7",
    "cb_liquidity_breadth_30",
)


def family_feature_names(family: str) -> list[str]:
    if family == "baseline":
        return []
    if family == "price":
        return list(PRICE_FEATURES)
    if family == "liquidity":
        return list(LIQUIDITY_FEATURES)
    if family == "combined":
        return [*PRICE_FEATURES, *LIQUIDITY_FEATURES]
    raise CrossExchangeConfirmationV48Error(
        f"unknown feature family: {family}"
    )


def _return(values: np.ndarray, index: int, lag: int) -> float:
    prior = float(values[index - lag])
    current = float(values[index])
    if prior <= 0.0 or current <= 0.0:
        raise CrossExchangeConfirmationV48Error(
            "non-positive price in return calculation"
        )
    return current / prior - 1.0


def _volume_change(values: np.ndarray, index: int, lag: int) -> float:
    prior = float(values[index - lag])
    current = float(values[index])
    if prior < 0.0 or current < 0.0:
        raise CrossExchangeConfirmationV48Error(
            "negative volume in liquidity calculation"
        )
    return float(np.clip(
        (current + 1.0) / (prior + 1.0) - 1.0,
        -10.0,
        10.0,
    ))


def _volume_share(
    coinbase: np.ndarray,
    binance: np.ndarray,
    index: int,
) -> float:
    left = max(float(coinbase[index]), 0.0)
    right = max(float(binance[index]), 0.0)
    return left / max(left + right, 1e-12)


def date_feature_map(
    states: dict[str, Any],
    history: CoinbaseHistory,
    family: str,
) -> dict[datetime, list[float]]:
    names = family_feature_names(family)
    if not names:
        return {}
    dates, arrays = state_arrays(states)
    if dates[0] < COINBASE_START or dates[-1] > COINBASE_END:
        raise CrossExchangeConfirmationV48Error(
            "Coinbase fixed range does not cover crypto state dates"
        )
    cb_close: dict[str, np.ndarray] = {}
    cb_volume: dict[str, np.ndarray] = {}
    for asset in COINBASE_PRODUCTS:
        try:
            rows = [history.bars[asset][stamp] for stamp in dates]
        except KeyError as exc:
            raise CrossExchangeConfirmationV48Error(
                f"Coinbase {asset} unavailable for common crypto date"
            ) from exc
        cb_close[asset] = np.asarray(
            [float(row.close) for row in rows],
            dtype=float,
        )
        cb_volume[asset] = np.asarray(
            [float(row.quote_volume) for row in rows],
            dtype=float,
        )

    bin_close = {
        asset: arrays[asset]["spot_close"]
        for asset in COINBASE_PRODUCTS
    }
    bin_volume = {
        asset: arrays[asset]["spot_volume"]
        for asset in COINBASE_PRODUCTS
    }
    feature_by_date: dict[datetime, list[float]] = {}
    for index in range(30, len(dates)):
        cb_returns = {
            (asset, lag): _return(cb_close[asset], index, lag)
            for asset in COINBASE_PRODUCTS
            for lag in (1, 7, 30)
        }
        bin_returns = {
            (asset, lag): _return(bin_close[asset], index, lag)
            for asset in COINBASE_PRODUCTS
            for lag in (1, 7, 30)
        }
        premium = {
            asset: (
                float(cb_close[asset][index])
                / float(bin_close[asset][index])
                - 1.0
            )
            for asset in COINBASE_PRODUCTS
        }
        prior_premium = {
            (asset, lag): (
                float(cb_close[asset][index - lag])
                / float(bin_close[asset][index - lag])
                - 1.0
            )
            for asset in COINBASE_PRODUCTS
            for lag in (1, 7)
        }
        share = {
            asset: _volume_share(
                cb_volume[asset],
                bin_volume[asset],
                index,
            )
            for asset in COINBASE_PRODUCTS
        }
        prior_share = {
            (asset, lag): _volume_share(
                cb_volume[asset],
                bin_volume[asset],
                index - lag,
            )
            for asset in COINBASE_PRODUCTS
            for lag in (1, 7)
        }
        aggregate_cb = sum(
            float(cb_volume[asset][index])
            for asset in COINBASE_PRODUCTS
        )
        aggregate_bin = sum(
            float(bin_volume[asset][index])
            for asset in COINBASE_PRODUCTS
        )
        aggregate_share = aggregate_cb / max(
            aggregate_cb + aggregate_bin,
            1e-12,
        )
        prior_aggregate_share: dict[int, float] = {}
        for lag in (1, 7):
            prior_cb = sum(
                float(cb_volume[asset][index - lag])
                for asset in COINBASE_PRODUCTS
            )
            prior_bin = sum(
                float(bin_volume[asset][index - lag])
                for asset in COINBASE_PRODUCTS
            )
            prior_aggregate_share[lag] = prior_cb / max(
                prior_cb + prior_bin,
                1e-12,
            )

        price_values = [
            cb_returns[("BTC", 1)],
            cb_returns[("BTC", 7)],
            cb_returns[("BTC", 30)],
            cb_returns[("ETH", 1)],
            cb_returns[("ETH", 7)],
            cb_returns[("ETH", 30)],
            float(np.mean([
                cb_returns[("BTC", 7)],
                cb_returns[("ETH", 7)],
            ])),
            float(np.mean([
                cb_returns[("BTC", 30)],
                cb_returns[("ETH", 30)],
            ])),
            cb_returns[("BTC", 1)] - bin_returns[("BTC", 1)],
            cb_returns[("BTC", 7)] - bin_returns[("BTC", 7)],
            cb_returns[("BTC", 30)] - bin_returns[("BTC", 30)],
            cb_returns[("ETH", 1)] - bin_returns[("ETH", 1)],
            cb_returns[("ETH", 7)] - bin_returns[("ETH", 7)],
            cb_returns[("ETH", 30)] - bin_returns[("ETH", 30)],
            premium["BTC"],
            premium["ETH"],
            premium["BTC"] - prior_premium[("BTC", 1)],
            premium["BTC"] - prior_premium[("BTC", 7)],
            premium["ETH"] - prior_premium[("ETH", 1)],
            premium["ETH"] - prior_premium[("ETH", 7)],
            float(np.mean([
                np.sign(cb_returns[(asset, 7)])
                == np.sign(bin_returns[(asset, 7)])
                for asset in COINBASE_PRODUCTS
            ])),
            float(np.mean([
                np.sign(cb_returns[(asset, 30)])
                == np.sign(bin_returns[(asset, 30)])
                for asset in COINBASE_PRODUCTS
            ])),
        ]
        liquidity_values = [
            _volume_change(cb_volume["BTC"], index, 1),
            _volume_change(cb_volume["BTC"], index, 7),
            _volume_change(cb_volume["BTC"], index, 30),
            _volume_change(cb_volume["ETH"], index, 1),
            _volume_change(cb_volume["ETH"], index, 7),
            _volume_change(cb_volume["ETH"], index, 30),
            share["BTC"],
            share["ETH"],
            share["BTC"] - prior_share[("BTC", 1)],
            share["BTC"] - prior_share[("BTC", 7)],
            share["ETH"] - prior_share[("ETH", 1)],
            share["ETH"] - prior_share[("ETH", 7)],
            aggregate_share,
            aggregate_share - prior_aggregate_share[1],
            aggregate_share - prior_aggregate_share[7],
            float(np.mean([
                _volume_change(cb_volume[asset], index, 7) > 0.0
                for asset in COINBASE_PRODUCTS
            ])),
            float(np.mean([
                _volume_change(cb_volume[asset], index, 30) > 0.0
                for asset in COINBASE_PRODUCTS
            ])),
        ]
        if family == "price":
            values = price_values
        elif family == "liquidity":
            values = liquidity_values
        else:
            values = [*price_values, *liquidity_values]
        if len(values) != len(names):
            raise CrossExchangeConfirmationV48Error(
                f"{family} feature length mismatch"
            )
        if not all(np.isfinite(values)):
            raise CrossExchangeConfirmationV48Error(
                f"non-finite {family} feature on {dates[index].date()}"
            )
        feature_by_date[dates[index]] = [
            float(value) for value in values
        ]
    return feature_by_date


def augmented_dataset(
    base: Dataset,
    states: dict[str, Any],
    history: CoinbaseHistory,
    family: str,
) -> Dataset:
    added_names = family_feature_names(family)
    if not added_names:
        return base
    features = date_feature_map(states, history, family)
    try:
        added = np.asarray(
            [features[stamp] for stamp in base.dates],
            dtype=float,
        )
    except KeyError as exc:
        raise CrossExchangeConfirmationV48Error(
            "cross-exchange features do not cover base dataset"
        ) from exc
    if added.shape != (len(base.X), len(added_names)):
        raise CrossExchangeConfirmationV48Error(
            f"unexpected added feature shape: {added.shape}"
        )
    return Dataset(
        X=np.hstack([base.X, added]),
        return1=base.return1,
        return3=base.return3,
        return7=base.return7,
        rank3=base.rank3,
        meta=base.meta,
        downside3=base.downside3,
        regimes=base.regimes,
        dates=list(base.dates),
        assets=list(base.assets),
        feature_names=[*base.feature_names, *added_names],
    )


def _compound(values: list[float]) -> float:
    return float(np.prod([1.0 + value for value in values]) - 1.0)


def candidate_eligibility(
    fold_results: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    standard_excess = [
        float(value["candidate_standard"]["net_return"])
        - float(value["control_standard"]["net_return"])
        for value in fold_results
    ]
    stress_excess = [
        float(value["candidate_stress"]["net_return"])
        - float(value["control_stress"]["net_return"])
        for value in fold_results
    ]
    if sum(value > 0.0 for value in standard_excess) < 4:
        reasons.append("fewer_than_four_positive_standard_excess_folds")
    if _compound(standard_excess) <= 0.0:
        reasons.append("non_positive_compounded_standard_excess")
    if _compound(stress_excess) <= 0.0:
        reasons.append("non_positive_compounded_stress_excess")
    if min(standard_excess) < -0.005:
        reasons.append(
            "worst_standard_fold_excess_below_minus_0_50_percent"
        )
    for value in fold_results:
        candidate_drawdown = max(
            float(value["candidate_standard"]["maximum_drawdown"]),
            float(value["candidate_stress"]["maximum_drawdown"]),
        )
        control_drawdown = max(
            float(value["control_standard"]["maximum_drawdown"]),
            float(value["control_stress"]["maximum_drawdown"]),
        )
        if candidate_drawdown > control_drawdown + 0.005:
            reasons.append("drawdown_allowance_exceeded")
            break
    if any(
        float(value["candidate_standard"]["maximum_target_exposure"])
        > 0.1000001
        or float(value["candidate_stress"]["maximum_target_exposure"])
        > 0.1000001
        for value in fold_results
    ):
        reasons.append("target_exposure_exceeded")
    return not reasons, reasons


def selection_key(
    fold_results: list[dict[str, Any]],
    family: str,
) -> tuple[float, ...]:
    standard_excess = [
        float(value["candidate_standard"]["net_return"])
        - float(value["control_standard"]["net_return"])
        for value in fold_results
    ]
    stress_excess = [
        float(value["candidate_stress"]["net_return"])
        - float(value["control_stress"]["net_return"])
        for value in fold_results
    ]
    maximum_drawdown = max(
        max(
            float(value["candidate_standard"]["maximum_drawdown"]),
            float(value["candidate_stress"]["maximum_drawdown"]),
        )
        for value in fold_results
    )
    turnover = sum(
        float(value["candidate_standard"]["turnover"])
        for value in fold_results
    )
    return (
        min(standard_excess),
        float(sum(value > 0.0 for value in standard_excess)),
        _compound(stress_excess),
        _compound(standard_excess),
        -maximum_drawdown,
        -turnover,
        -float(len(family_feature_names(family))),
        -float(ACTIVE_FAMILIES.index(family)),
    )


def run_walk_forward(
    base: Dataset,
    augmented: dict[str, Dataset],
    cash_history: v44.CashRateHistory,
) -> tuple[str, dict[str, Any]]:
    fold_records: list[dict[str, Any]] = []
    candidate_fold_results = {
        family: [] for family in ACTIVE_FAMILIES
    }
    for fold in v46.WALK_FORWARD_FOLDS:
        control_bundle, control_training = v46.train_base_bundle(
            base,
            fold,
        )
        control_predictions = v43.predict_components(
            control_bundle,
            base.X,
        )
        validation_mask = v46.date_mask(
            base,
            fold.validation_start,
            fold.validation_end,
        )
        control_standard = v44.simulate(
            base,
            validation_mask,
            control_bundle,
            control_predictions,
            cash_history,
            one_way_cost=STANDARD_ONE_WAY_COST,
        )
        control_stress = v44.simulate(
            base,
            validation_mask,
            control_bundle,
            control_predictions,
            cash_history,
            one_way_cost=STRESS_ONE_WAY_COST,
        )
        family_records: dict[str, Any] = {}
        for family in ACTIVE_FAMILIES:
            dataset = augmented[family]
            bundle, training = v46.train_base_bundle(dataset, fold)
            predictions = v43.predict_components(bundle, dataset.X)
            candidate_standard = v44.simulate(
                dataset,
                validation_mask,
                bundle,
                predictions,
                cash_history,
                one_way_cost=STANDARD_ONE_WAY_COST,
            )
            candidate_stress = v44.simulate(
                dataset,
                validation_mask,
                bundle,
                predictions,
                cash_history,
                one_way_cost=STRESS_ONE_WAY_COST,
            )
            values = {
                "name": fold.name,
                "control_standard": control_standard,
                "control_stress": control_stress,
                "candidate_standard": candidate_standard,
                "candidate_stress": candidate_stress,
            }
            candidate_fold_results[family].append(values)
            family_records[family] = {
                "training": training,
                "standard": candidate_standard,
                "stress": candidate_stress,
                "standard_excess": (
                    float(candidate_standard["net_return"])
                    - float(control_standard["net_return"])
                ),
                "stress_excess": (
                    float(candidate_stress["net_return"])
                    - float(control_stress["net_return"])
                ),
            }
        fold_records.append({
            "name": fold.name,
            "training_end": utc_iso(fold.training_end),
            "base_calibration_start": utc_iso(
                fold.base_calibration_start
            ),
            "base_calibration_end": utc_iso(
                fold.base_calibration_end
            ),
            "validation_start": utc_iso(fold.validation_start),
            "validation_end": utc_iso(fold.validation_end),
            "control_training": control_training,
            "control_standard": control_standard,
            "control_stress": control_stress,
            "families": family_records,
        })

    candidates: list[dict[str, Any]] = [{
        "family": "baseline",
        "eligible": True,
        "ineligibility_reasons": [],
        "selection_key": None,
        "positive_standard_excess_folds": 0,
        "minimum_standard_excess": 0.0,
        "compounded_standard_excess": 0.0,
        "compounded_stress_excess": 0.0,
        "added_feature_count": 0,
    }]
    eligible: list[tuple[tuple[float, ...], str]] = []
    for family in ACTIVE_FAMILIES:
        results = candidate_fold_results[family]
        allowed, reasons = candidate_eligibility(results)
        standard_excess = [
            float(value["candidate_standard"]["net_return"])
            - float(value["control_standard"]["net_return"])
            for value in results
        ]
        stress_excess = [
            float(value["candidate_stress"]["net_return"])
            - float(value["control_stress"]["net_return"])
            for value in results
        ]
        key = selection_key(results, family) if allowed else None
        candidates.append({
            "family": family,
            "eligible": allowed,
            "ineligibility_reasons": reasons,
            "selection_key": list(key) if key is not None else None,
            "positive_standard_excess_folds": sum(
                value > 0.0 for value in standard_excess
            ),
            "minimum_standard_excess": min(standard_excess),
            "compounded_standard_excess": _compound(
                standard_excess
            ),
            "compounded_stress_excess": _compound(stress_excess),
            "added_feature_count": len(
                family_feature_names(family)
            ),
        })
        if allowed and key is not None:
            eligible.append((key, family))
    selected = max(eligible)[1] if eligible else "baseline"
    return selected, {
        "selected_family": selected,
        "selected_is_disabled_baseline": selected == "baseline",
        "walk_forward_fold_count": len(fold_records),
        "candidate_count": len(candidates),
        "eligible_active_candidate_count": len(eligible),
        "candidates": candidates,
        "folds": fold_records,
    }


def run_campaign(
    baseline_report: dict[str, Any],
    final_control_bundle: v43.Bundle,
    states: dict[str, Any] | None = None,
    source_report: dict[str, Any] | None = None,
    cash_history: v44.CashRateHistory | None = None,
    coinbase_history: CoinbaseHistory | None = None,
    *,
    baseline_bundle_sha256: str | None = None,
    monthly_workers: int = 24,
    metrics_workers: int = 48,
) -> dict[str, Any]:
    v44_reproduce.validate_baseline_report(baseline_report)
    if states is None:
        states, source_report = load_all_sources(
            monthly_workers=monthly_workers,
            metrics_workers=metrics_workers,
        )
    if source_report is None:
        raise CrossExchangeConfirmationV48Error(
            "current Binance source report is unavailable"
        )
    if canonical_json(source_report) != canonical_json(
        baseline_report["source"]
    ):
        raise CrossExchangeConfirmationV48Error(
            "current Binance source inventory differs from frozen v4.3"
        )
    base = build_dataset(states)
    observed_dataset = {
        "row_count": len(base.X),
        "date_count": len(set(base.dates)),
        "first_date": utc_iso(min(base.dates)),
        "last_date": utc_iso(max(base.dates)),
        "feature_count": len(base.feature_names),
        "training_end": utc_iso(v43.TRAIN_END),
        "calibration_start": utc_iso(v43.CALIBRATION_START),
        "calibration_end": utc_iso(v43.CALIBRATION_END),
    }
    if canonical_json(observed_dataset) != canonical_json(
        baseline_report["dataset"]
    ):
        raise CrossExchangeConfirmationV48Error(
            "current base dataset differs from frozen v4.3"
        )
    if canonical_json(
        v43.bundle_summary(final_control_bundle)
    ) != canonical_json(baseline_report["bundle"]):
        raise CrossExchangeConfirmationV48Error(
            "final control bundle differs from frozen v4.3"
        )
    reproduced_v43 = v43.evaluate_sealed(
        base,
        final_control_bundle,
    )
    if canonical_json(reproduced_v43) != canonical_json(
        baseline_report["evaluation"]
    ):
        raise CrossExchangeConfirmationV48Error(
            "final control bundle does not reproduce frozen v4.3"
        )
    if cash_history is None:
        cash_history = v44.load_cash_history()
    if coinbase_history is None:
        coinbase_history = download_coinbase_history()

    augmented = {
        family: augmented_dataset(
            base,
            states,
            coinbase_history,
            family,
        )
        for family in ACTIVE_FAMILIES
    }
    selected_family, selection = run_walk_forward(
        base,
        augmented,
        cash_history,
    )
    v44_baseline = v44.evaluate_sealed(
        base,
        final_control_bundle,
        cash_history,
        baseline=reproduced_v43,
    )

    if selected_family == "baseline":
        final_bundle = final_control_bundle
        final_calibration = baseline_report["calibration"]
        evaluation = v44_baseline
        final_bundle_reused = True
    else:
        final_dataset = augmented[selected_family]
        final_bundle, final_calibration = v43.train_bundle(
            final_dataset
        )
        evaluation = v44.evaluate_sealed(
            final_dataset,
            final_bundle,
            cash_history,
        )
        final_bundle_reused = False

    comparison = {
        "standard_return_change": (
            float(evaluation["aggregate_standard_return"])
            - float(v44_baseline["aggregate_standard_return"])
        ),
        "stress_return_change": (
            float(evaluation["aggregate_stress_return"])
            - float(v44_baseline["aggregate_stress_return"])
        ),
        "annualized_return_change": (
            float(evaluation["annualized_standard_return"])
            - float(v44_baseline["annualized_standard_return"])
        ),
        "maximum_drawdown_change": (
            float(evaluation["maximum_drawdown"])
            - float(v44_baseline["maximum_drawdown"])
        ),
        "action_count_change": (
            int(evaluation["target_changing_actions"])
            - int(v44_baseline["target_changing_actions"])
        ),
    }
    added_names = family_feature_names(selected_family)
    augmented_metadata = {
        family: {
            "row_count": len(dataset.X),
            "feature_count": len(dataset.feature_names),
            "added_feature_count": len(
                family_feature_names(family)
            ),
            "feature_names_sha256": hashlib.sha256(
                canonical_json(dataset.feature_names).encode("utf-8")
            ).hexdigest(),
        }
        for family, dataset in augmented.items()
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_iso(datetime.now(timezone.utc)),
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": True,
        "retrospective": True,
        "untouched_historical_dates": False,
        "universe": list(ASSETS),
        "source": source_report,
        "coinbase_source": coinbase_history.source,
        "cash_source": cash_history.source,
        "runtime": v44.runtime_versions(),
        "base_dataset": observed_dataset,
        "augmented_datasets": augmented_metadata,
        "selected_feature_names": added_names,
        "selected_feature_names_sha256": hashlib.sha256(
            canonical_json(added_names).encode("utf-8")
        ).hexdigest(),
        "protocol_sha256": file_sha256(PROTOCOL_PATH),
        "implementation_contract_sha256": file_sha256(
            CONTRACT_PATH
        ),
        "implementation_sha256": file_sha256(
            Path(__file__).resolve()
        ),
        "baseline_report_sha256": baseline_report[
            "report_sha256"
        ],
        "baseline_bundle_sha256": baseline_bundle_sha256,
        "reproduction": {
            "source_inventory_exact": True,
            "dataset_metadata_exact": True,
            "bundle_summary_exact": True,
            "v43_evaluation_exact": True,
            "final_control_retrained_for_v48": False,
            "walk_forward_fold_count": len(
                v46.WALK_FORWARD_FOLDS
            ),
            "coinbase_future_observations_allowed": False,
        },
        "selection": selection,
        "final_bundle_reused_from_v43": final_bundle_reused,
        "final_bundle": v43.bundle_summary(final_bundle),
        "final_calibration": final_calibration,
        "v43_baseline": reproduced_v43,
        "v44_baseline": v44_baseline,
        "evaluation": evaluation,
        "v44_comparison": comparison,
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def parse_args(
    argv: list[str] | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run v4.8 cross-exchange confirmation research"
    )
    parser.add_argument(
        "--baseline-json",
        type=Path,
        required=True,
    )
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("evidence/v48/historical.json"),
    )
    parser.add_argument("--monthly-workers", type=int, default=24)
    parser.add_argument("--metrics-workers", type=int, default=48)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    baseline_report = json.loads(
        args.baseline_json.read_text(encoding="utf-8")
    )
    bundle = v44_reproduce.load_bundle(args.bundle)
    report = run_campaign(
        baseline_report,
        bundle,
        baseline_bundle_sha256=file_sha256(args.bundle),
        monthly_workers=max(1, args.monthly_workers),
        metrics_workers=max(1, args.metrics_workers),
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    evaluation = report["evaluation"]
    selection = report["selection"]
    print(json.dumps({
        "status": evaluation["status"],
        "report_sha256": report["report_sha256"],
        "selected_family": selection["selected_family"],
        "selected_is_disabled_baseline": (
            selection["selected_is_disabled_baseline"]
        ),
        "eligible_active_candidate_count": (
            selection["eligible_active_candidate_count"]
        ),
        "standard_return": evaluation[
            "aggregate_standard_return"
        ],
        "stress_return": evaluation[
            "aggregate_stress_return"
        ],
        "annualized_standard_return": evaluation[
            "annualized_standard_return"
        ],
        "maximum_drawdown": evaluation["maximum_drawdown"],
        "standard_return_change": report["v44_comparison"][
            "standard_return_change"
        ],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
