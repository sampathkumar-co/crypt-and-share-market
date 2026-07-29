from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from tradebot.models import Candle

REQUIRED_COLUMNS = {"timestamp", "open", "high", "low", "close", "volume"}


class CSVValidationError(ValueError):
    pass


def _parse_time(value: str) -> datetime:
    value = value.strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CSVValidationError(f"Invalid timestamp: {value}") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def load_candles(path: str | Path) -> list[Candle]:
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise CSVValidationError(f"CSV file not found: {file_path}")

    with file_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(set(reader.fieldnames)):
            raise CSVValidationError(f"CSV must include columns: {sorted(REQUIRED_COLUMNS)}")
        candles: list[Candle] = []
        seen_timestamps: set[datetime] = set()
        for line_no, row in enumerate(reader, start=2):
            try:
                candle = Candle(
                    timestamp=_parse_time(row["timestamp"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            except (TypeError, ValueError) as exc:
                raise CSVValidationError(f"Bad numeric data on line {line_no}") from exc
            if candle.timestamp in seen_timestamps:
                raise CSVValidationError(f"Duplicate timestamp on line {line_no}: {candle.timestamp.isoformat()}")
            seen_timestamps.add(candle.timestamp)
            if min(candle.open, candle.high, candle.low, candle.close) <= 0 or candle.volume < 0:
                raise CSVValidationError(f"Invalid OHLCV values on line {line_no}")
            if candle.high < max(candle.open, candle.close) or candle.low > min(candle.open, candle.close):
                raise CSVValidationError(f"Inconsistent high/low on line {line_no}")
            candles.append(candle)

    if len(candles) < 5:
        raise CSVValidationError("At least 5 candles are required")
    return sorted(candles, key=lambda candle: candle.timestamp)


def audit_candles(candles: list[Candle]) -> dict[str, int | float | str | None]:
    if not candles:
        raise CSVValidationError("Cannot audit empty candle data")
    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    deltas = [
        (current.timestamp - previous.timestamp).total_seconds()
        for previous, current in zip(ordered, ordered[1:])
    ]
    typical_interval = float(median(deltas)) if deltas else 0.0
    missing_intervals = 0
    if typical_interval > 0:
        missing_intervals = sum(max(0, round(delta / typical_interval) - 1) for delta in deltas if delta > typical_interval * 1.5)
    return {
        "candles": len(ordered),
        "start": ordered[0].timestamp.isoformat(),
        "end": ordered[-1].timestamp.isoformat(),
        "typical_interval_seconds": typical_interval,
        "missing_intervals_estimate": missing_intervals,
        "zero_volume_candles": sum(1 for candle in ordered if candle.volume == 0),
        "price_return": (ordered[-1].close - ordered[0].close) / ordered[0].close,
    }
