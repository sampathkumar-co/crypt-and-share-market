from __future__ import annotations
import csv
from datetime import datetime
from pathlib import Path
from tradebot.models import Candle

REQUIRED_COLUMNS = {"timestamp", "open", "high", "low", "close", "volume"}

class CSVValidationError(ValueError):
    pass

def _parse_time(value: str) -> datetime:
    value = value.strip()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CSVValidationError(f"Invalid timestamp: {value}") from exc

def load_candles(path: str | Path) -> list[Candle]:
    file_path = Path(path)
    if not file_path.exists():
        raise CSVValidationError(f"CSV file not found: {file_path}")
    with file_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(set(reader.fieldnames)):
            raise CSVValidationError(f"CSV must include columns: {sorted(REQUIRED_COLUMNS)}")
        candles: list[Candle] = []
        for line_no, row in enumerate(reader, start=2):
            try:
                candle = Candle(
                    timestamp=_parse_time(row["timestamp"]),
                    open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            except (TypeError, ValueError) as exc:
                raise CSVValidationError(f"Bad numeric data on line {line_no}") from exc
            if min(candle.open, candle.high, candle.low, candle.close) <= 0 or candle.volume < 0:
                raise CSVValidationError(f"Invalid OHLCV values on line {line_no}")
            if candle.high < max(candle.open, candle.close) or candle.low > min(candle.open, candle.close):
                raise CSVValidationError(f"Inconsistent high/low on line {line_no}")
            candles.append(candle)
    if len(candles) < 5:
        raise CSVValidationError("At least 5 candles are required")
    return sorted(candles, key=lambda c: c.timestamp)
