from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradebot.backtest.research_gate import dataset_fingerprint
from tradebot.data import crypto_4h_provider as base
from tradebot.data.crypto_external_factors import sha256_file
from tradebot.data.crypto_provider import save_candles_csv
from tradebot.models import Candle

MAX_MISSING_HOURS_PER_ASSET = 6
MAX_MISSING_FRACTION = 0.00035
MAX_CONSECUTIVE_MISSING_HOURS = 6
CONTINUITY_PROTOCOL_COMMIT = "6dc91831b3f5278a062b4b4ab669a48dbe58cbdc"


class BoundedContinuityError(base.FourHourDataError):
    """Raised when Coinbase omissions exceed the frozen v1.4.2 contract."""


def expected_hourly_timestamps(
    start: datetime = base.EXPECTED_START,
    end_exclusive: datetime = base.EXPECTED_END_EXCLUSIVE,
) -> list[datetime]:
    count = int((end_exclusive - start).total_seconds() // 3600)
    return [start + timedelta(hours=index) for index in range(count)]


def _missing_runs(missing: list[datetime]) -> list[list[datetime]]:
    runs: list[list[datetime]] = []
    current: list[datetime] = []
    previous: datetime | None = None
    for timestamp in missing:
        if previous is None or timestamp == previous + timedelta(hours=1):
            current.append(timestamp)
        else:
            runs.append(current)
            current = [timestamp]
        previous = timestamp
    if current:
        runs.append(current)
    return runs


def apply_bounded_continuity(
    raw_hourly: list[Candle],
    start: datetime = base.EXPECTED_START,
    end_exclusive: datetime = base.EXPECTED_END_EXCLUSIVE,
) -> tuple[list[Candle], list[datetime], int]:
    expected = expected_hourly_timestamps(start, end_exclusive)
    observed = {candle.timestamp: candle for candle in raw_hourly}
    if len(observed) != len(raw_hourly):
        raise BoundedContinuityError("Duplicate Coinbase hourly timestamp")
    unexpected = sorted(set(observed) - set(expected))
    if unexpected:
        raise BoundedContinuityError(
            f"Coinbase returned timestamps outside the frozen interval: {unexpected[:3]}"
        )
    missing = [timestamp for timestamp in expected if timestamp not in observed]
    runs = _missing_runs(missing)
    longest = max((len(run) for run in runs), default=0)
    if len(missing) > MAX_MISSING_HOURS_PER_ASSET:
        raise BoundedContinuityError(
            f"{len(missing)} missing hours exceed the frozen maximum of "
            f"{MAX_MISSING_HOURS_PER_ASSET}"
        )
    if expected and len(missing) / len(expected) > MAX_MISSING_FRACTION:
        raise BoundedContinuityError("Missing-hour fraction exceeds the frozen maximum")
    if longest > MAX_CONSECUTIVE_MISSING_HOURS:
        raise BoundedContinuityError(
            f"Consecutive gap of {longest} hours exceeds the frozen maximum"
        )
    if missing and (expected[0] in missing or expected[-1] in missing):
        raise BoundedContinuityError("The first or final frozen hour is missing")

    completed: list[Candle] = []
    previous_close: float | None = None
    for timestamp in expected:
        candle = observed.get(timestamp)
        if candle is None:
            if previous_close is None:
                raise BoundedContinuityError("No earlier close exists for continuity")
            candle = Candle(
                timestamp=timestamp,
                open=previous_close,
                high=previous_close,
                low=previous_close,
                close=previous_close,
                volume=0.0,
            )
        completed.append(candle)
        previous_close = candle.close
    if len(completed) != len(expected):
        raise BoundedContinuityError("Completed hourly grid has the wrong length")
    return completed, missing, longest


def fetch_v142_four_hour_bundle(out_dir: str | Path) -> dict[str, object]:
    root = Path(out_dir)
    raw_dir = root / "raw_hourly"
    completed_dir = root / "hourly"
    four_hour_dir = root / "four_hour"
    raw_dir.mkdir(parents=True, exist_ok=True)
    completed_dir.mkdir(parents=True, exist_ok=True)
    four_hour_dir.mkdir(parents=True, exist_ok=True)

    sources: list[dict[str, object]] = []
    histories: dict[str, list[Candle]] = {}
    for symbol, product in base.SYMBOL_TO_PRODUCT.items():
        raw = base.fetch_coinbase_hourly(
            product,
            base.EXPECTED_START,
            base.EXPECTED_END_EXCLUSIVE,
        )
        completed, synthetic, longest = apply_bounded_continuity(raw)
        four_hour = base.aggregate_hourly_to_four_hour(completed)
        if len(completed) != base.EXPECTED_HOURLY_BARS:
            raise BoundedContinuityError(
                f"{symbol} completed grid has {len(completed)} hours; "
                f"{base.EXPECTED_HOURLY_BARS} required"
            )
        if len(four_hour) != base.EXPECTED_FOUR_HOUR_BARS:
            raise BoundedContinuityError(
                f"{symbol} has {len(four_hour)} four-hour candles; "
                f"{base.EXPECTED_FOUR_HOUR_BARS} required"
            )
        if four_hour[0].timestamp != base.EXPECTED_START:
            raise BoundedContinuityError(f"Unexpected first four-hour candle for {symbol}")
        expected_last = base.EXPECTED_END_EXCLUSIVE - timedelta(hours=4)
        if four_hour[-1].timestamp != expected_last:
            raise BoundedContinuityError(f"Unexpected final four-hour candle for {symbol}")

        raw_path = save_candles_csv(symbol, raw, raw_dir)
        completed_path = save_candles_csv(symbol, completed, completed_dir)
        four_hour_path = save_candles_csv(symbol, four_hour, four_hour_dir)
        histories[symbol] = four_hour
        sources.append(
            {
                "symbol": symbol,
                "product": product,
                "raw_hourly_path": raw_path.relative_to(root).as_posix(),
                "hourly_path": completed_path.relative_to(root).as_posix(),
                "four_hour_path": four_hour_path.relative_to(root).as_posix(),
                "raw_hourly_sha256": sha256_file(raw_path),
                "hourly_sha256": sha256_file(completed_path),
                "four_hour_sha256": sha256_file(four_hour_path),
                "raw_hourly_rows": len(raw),
                "hourly_rows": len(completed),
                "four_hour_rows": len(four_hour),
                "synthetic_hourly_rows": len(synthetic),
                "synthetic_timestamps": [timestamp.isoformat() for timestamp in synthetic],
                "longest_synthetic_run_hours": longest,
                "first_hour": completed[0].timestamp.isoformat(),
                "last_hour": completed[-1].timestamp.isoformat(),
                "first_four_hour": four_hour[0].timestamp.isoformat(),
                "last_four_hour": four_hour[-1].timestamp.isoformat(),
            }
        )

    common = set.intersection(
        *(set(candle.timestamp for candle in histories[symbol]) for symbol in base.REQUIRED_SYMBOLS)
    )
    if len(common) != base.EXPECTED_FOUR_HOUR_BARS:
        raise BoundedContinuityError(
            f"Only {len(common)} common four-hour timestamps are available"
        )
    manifest: dict[str, object] = {
        "schema_version": "1.0",
        "continuity_schema_version": "1.0",
        "continuity_protocol_commit": CONTINUITY_PROTOCOL_COMMIT,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "requested_start": base.EXPECTED_START.isoformat(),
        "requested_end_exclusive": base.EXPECTED_END_EXCLUSIVE.isoformat(),
        "sources": sources,
        "dataset_fingerprint": dataset_fingerprint(histories),
        "continuity_limits": {
            "max_missing_hours_per_asset": MAX_MISSING_HOURS_PER_ASSET,
            "max_missing_fraction": MAX_MISSING_FRACTION,
            "max_consecutive_missing_hours": MAX_CONSECUTIVE_MISSING_HOURS,
            "synthetic_ohlcv": "previous_close,previous_close,previous_close,previous_close,0",
        },
        "price_returns_calculated": False,
        "strategy_returns_calculated": False,
        "holdout_returns_calculated": False,
        "paper_only": True,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return manifest


def verify_v142_manifest(root: str | Path) -> dict[str, object]:
    base_path = Path(root)
    payload = base.verify_v14_four_hour_manifest(base_path)
    if payload.get("continuity_protocol_commit") != CONTINUITY_PROTOCOL_COMMIT:
        raise BoundedContinuityError("Continuity protocol commit changed")
    if payload.get("price_returns_calculated") is not False:
        raise BoundedContinuityError("Price-return flag changed")
    sources = payload.get("sources", [])
    for item in sources:
        raw_path = base_path / item["raw_hourly_path"]
        if not raw_path.is_file():
            raise BoundedContinuityError(f"Missing raw hourly file for {item['symbol']}")
        if sha256_file(raw_path) != item["raw_hourly_sha256"]:
            raise BoundedContinuityError(f"Raw hourly hash mismatch for {item['symbol']}")
        if item["synthetic_hourly_rows"] != len(item["synthetic_timestamps"]):
            raise BoundedContinuityError(f"Synthetic metadata mismatch for {item['symbol']}")
        if item["synthetic_hourly_rows"] > MAX_MISSING_HOURS_PER_ASSET:
            raise BoundedContinuityError(f"Too many synthetic hours for {item['symbol']}")
        if item["longest_synthetic_run_hours"] > MAX_CONSECUTIVE_MISSING_HOURS:
            raise BoundedContinuityError(f"Synthetic run too long for {item['symbol']}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch frozen v1.4.2 Coinbase data with bounded continuity"
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    manifest = fetch_v142_four_hour_bundle(args.out)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
