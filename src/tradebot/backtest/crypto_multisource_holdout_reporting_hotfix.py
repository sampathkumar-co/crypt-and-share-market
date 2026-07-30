from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from tradebot.backtest import crypto_multifactor as base
from tradebot.backtest import crypto_multisource_holdout as frozen
from tradebot.backtest.research_gate import dataset_fingerprint
from tradebot.models import Candle, Market


def _report_boundaries(
    evaluation: dict[str, list[Candle]],
    config: frozen.MultiSourceHoldoutConfig,
) -> tuple[datetime, datetime, datetime, datetime]:
    base_config = frozen._base_config(config)
    period_1_start, _, _ = base._period_bounds(1, base_config)
    _, period_3_end, _ = base._period_bounds(3, base_config)
    candles = evaluation[next(iter(evaluation))]
    return (
        candles[period_1_start].timestamp,
        candles[period_3_end].timestamp,
        candles[period_3_end + 1].timestamp,
        candles[-1].timestamp,
    )


def evaluate_multisource_holdout_reporting_hotfix(
    price_folder: str | Path,
    external_folder: str | Path,
    market: Market = Market.CRYPTO,
    config: frozen.MultiSourceHoldoutConfig | None = None,
) -> frozen.MultiSourceHoldoutReport:
    if market != Market.CRYPTO:
        raise ValueError("v1.3 is frozen to crypto")
    config = config or frozen.MultiSourceHoldoutConfig()
    full, evaluation = frozen._load_exact_prices(price_folder, config)
    store = frozen._load_external_store(external_folder)
    covered = frozen._validate_external_coverage(store)

    summaries = [
        frozen._variant_result(evaluation, variant, config, store)
        for variant in frozen.VARIANTS
    ]
    by_name = {summary.variant: summary for summary in summaries}
    primary = by_name["primary_multisource"]
    raw = by_name["raw_simple_trend"]
    first_two = mean(period.net_return for period in primary.periods[:2])
    last_two = mean(period.net_return for period in primary.periods[1:])
    beats_raw_fraction = sum(
        left.net_return > right.net_return
        for left, right in zip(primary.periods, raw.periods)
    ) / len(primary.periods)
    selected = sorted(
        {
            symbol
            for period in primary.periods
            for symbol in period.selected_symbols
        }
    )
    ablation_names = (
        "without_stablecoin",
        "without_onchain",
        "without_derivatives",
        "without_macro",
    )
    positive_ablations = sum(
        by_name[name].average_return > 0 for name in ablation_names
    )

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
    test_start, test_end, embargo_start, embargo_end = _report_boundaries(
        evaluation, config
    )
    if (
        test_start.date() != frozen.EXPECTED_HOLDOUT_START
        or test_end.date() != frozen.EXPECTED_TEST_END
        or embargo_start.date() != frozen.EXPECTED_EMBARGO_START
        or embargo_end.date() != frozen.EXPECTED_HOLDOUT_END
    ):
        raise ValueError("Holdout or embargo boundaries changed")

    return frozen.MultiSourceHoldoutReport(
        schema_version=frozen.SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        market=market.value,
        symbols=sorted(full),
        price_dataset_fingerprint=dataset_fingerprint(full),
        external_manifest_fingerprint=store.manifest_fingerprint,
        price_start=frozen.EXPECTED_FULL_START,
        price_end=frozen.EXPECTED_FULL_END,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Materialise the consumed v1.3.1 holdout report"
    )
    parser.add_argument("--price-folder", required=True)
    parser.add_argument("--external-folder", required=True)
    parser.add_argument(
        "--market",
        choices=[market.value for market in Market],
        default=Market.CRYPTO.value,
    )
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args(argv)
    report = evaluate_multisource_holdout_reporting_hotfix(
        args.price_folder,
        args.external_folder,
        Market(args.market),
    )
    payload: dict[str, Any] = asdict(report)
    frozen._write_json(args.json_out, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
