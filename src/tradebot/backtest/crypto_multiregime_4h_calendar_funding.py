from __future__ import annotations

import argparse
import json
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Iterator
from unittest.mock import patch

from tradebot.backtest import crypto_multiregime_4h as base
from tradebot.models import Market

FOUR_HOURS = timedelta(hours=4)
SEVEN_DAY_BUCKETS = 7 * 6
HISTORY_BUCKETS = 120 * 6
REPORT_SCHEMA_VERSION = "1.4.2"
_SNAPSHOT_CACHE: dict[
    tuple[int, str], dict[datetime, tuple[float, float, float]]
] = {}


def build_calendar_funding_snapshots(
    series: dict[datetime, float],
) -> dict[datetime, tuple[float, float, float]]:
    """Build look-ahead-safe funding snapshots on a complete four-hour grid.

    Hyperliquid settlement cadence varies across products and history. A missing
    four-hour bucket therefore represents zero settled funding cashflow rather
    than a missing observation. Each current value is the mean of exactly 42
    calendar buckets. Its percentile and median use exactly 720 prior rolling
    seven-day means, corresponding to 120 calendar days.
    """

    if not series:
        return {}
    bucketed = {
        base._four_hour_bucket(timestamp): float(value)
        for timestamp, value in series.items()
    }
    start = min(bucketed)
    end = max(bucketed)
    rolling_values: deque[float] = deque()
    rolling_total = 0.0
    seven_day_means: dict[datetime, float] = {}
    anchor = start
    while anchor <= end:
        value = bucketed.get(anchor, 0.0)
        rolling_values.append(value)
        rolling_total += value
        if len(rolling_values) > SEVEN_DAY_BUCKETS:
            rolling_total -= rolling_values.popleft()
        if len(rolling_values) == SEVEN_DAY_BUCKETS:
            seven_day_means[anchor] = rolling_total / SEVEN_DAY_BUCKETS
        anchor += FOUR_HOURS

    history: deque[tuple[datetime, float]] = deque()
    snapshots: dict[datetime, tuple[float, float, float]] = {}
    for anchor, current in seven_day_means.items():
        if len(history) == HISTORY_BUCKETS:
            values = [value for _, value in history]
            snapshots[anchor] = (
                current,
                base._percentile(values, 0.10),
                median(values),
            )
        history.append((anchor, current))
        if len(history) > HISTORY_BUCKETS:
            history.popleft()
    return snapshots


def calendar_funding_snapshot(
    store: base.ExternalStore,
    symbol: str,
    as_of: datetime,
) -> tuple[float, float, float] | None:
    key = (id(store), symbol)
    snapshots = _SNAPSHOT_CACHE.get(key)
    if snapshots is None:
        snapshots = build_calendar_funding_snapshots(
            store.funding.get(symbol, {})
        )
        _SNAPSHOT_CACHE[key] = snapshots
    return snapshots.get(base._four_hour_bucket(as_of))


@contextmanager
def calendar_funding_model() -> Iterator[None]:
    with patch.object(base, "_funding_snapshot", calendar_funding_snapshot):
        yield


def _label_report(report: base.MultiRegimeReport) -> base.MultiRegimeReport:
    report.schema_version = REPORT_SCHEMA_VERSION
    return report


def evaluate_discovery(
    price_folder: str | Path,
    external_folder: str | Path,
    market: Market = Market.CRYPTO,
    config: base.MultiRegimeConfig | None = None,
) -> base.MultiRegimeReport:
    with calendar_funding_model():
        report = base.evaluate_discovery(
            price_folder,
            external_folder,
            market,
            config,
        )
    return _label_report(report)


def evaluate_holdout(
    price_folder: str | Path,
    external_folder: str | Path,
    discovery_json: str | Path,
    market: Market = Market.CRYPTO,
    config: base.MultiRegimeConfig | None = None,
) -> base.MultiRegimeReport:
    with calendar_funding_model():
        report = base.evaluate_holdout(
            price_folder,
            external_folder,
            discovery_json,
            market,
            config,
        )
    return _label_report(report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate frozen v1.4.2 multi-regime research with "
            "calendar-normalized settled funding"
        )
    )
    parser.add_argument("--price-folder", required=True)
    parser.add_argument("--external-folder", required=True)
    parser.add_argument("--json-out", required=True)
    parser.add_argument(
        "--mode", choices=("discovery", "holdout"), default="discovery"
    )
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
        report = evaluate_discovery(
            args.price_folder,
            args.external_folder,
        )
    payload = asdict(report)
    base._write_json(args.json_out, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
