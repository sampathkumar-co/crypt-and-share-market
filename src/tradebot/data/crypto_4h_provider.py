from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tradebot.backtest.research_gate import dataset_fingerprint
from tradebot.data.crypto_external_factors import sha256_file
from tradebot.data.crypto_provider import save_candles_csv
from tradebot.models import Candle

COINBASE_BASE_URL = "https://api.exchange.coinbase.com"
HOURLY_GRANULARITY = 3_600
FOUR_HOUR_SECONDS = 14_400
MAX_CANDLES_PER_REQUEST = 300
EXPECTED_START = datetime(2023, 6, 7, 0, 0)
EXPECTED_END_EXCLUSIVE = datetime(2025, 11, 23, 0, 0)
EXPECTED_FOUR_HOUR_BARS = 5_400
EXPECTED_HOURLY_BARS = EXPECTED_FOUR_HOUR_BARS * 4

SYMBOL_TO_PRODUCT = {
    "AVAXUSDT": "AVAX-USD",
    "DOTUSDT": "DOT-USD",
    "NEARUSDT": "NEAR-USD",
    "FILUSDT": "FIL-USD",
    "ICPUSDT": "ICP-USD",
    "OPUSDT": "OP-USD",
    "ARBUSDT": "ARB-USD",
    "SUIUSDT": "SUI-USD",
}
REQUIRED_SYMBOLS = tuple(SYMBOL_TO_PRODUCT)


class FourHourDataError(RuntimeError):
    """Raised when the frozen v1.4 market-data contract cannot be satisfied."""


@dataclass(frozen=True)
class FourHourSource:
    symbol: str
    product: str
    hourly_path: str
    four_hour_path: str
    hourly_sha256: str
    four_hour_sha256: str
    hourly_rows: int
    four_hour_rows: int
    first_hour: str
    last_hour: str
    first_four_hour: str
    last_four_hour: str


@dataclass(frozen=True)
class FourHourManifest:
    schema_version: str
    retrieved_at: str
    requested_start: str
    requested_end_exclusive: str
    sources: list[FourHourSource]
    dataset_fingerprint: str
    paper_only: bool = True


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _naive_utc(value: datetime) -> datetime:
    return _utc(value).replace(tzinfo=None)


def _request_json(url: str, *, timeout: float = 35.0, retries: int = 6) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "crypt-and-share-market-paper-research/1.4",
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed public data URL
                if response.status != 200:
                    raise FourHourDataError(f"Coinbase returned HTTP {response.status}")
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, FourHourDataError) as exc:
            last_error = exc
            retryable = not isinstance(exc, HTTPError) or exc.code in {429, 500, 502, 503, 504}
            if not retryable or attempt == retries:
                break
            time.sleep(min(2.0 * attempt, 12.0))
    raise FourHourDataError(f"Coinbase request failed: {last_error}")


def _request_hourly_page(product: str, start: datetime, end: datetime) -> list[Any]:
    query = urlencode(
        {
            "start": _utc(start).isoformat().replace("+00:00", "Z"),
            "end": _utc(end).isoformat().replace("+00:00", "Z"),
            "granularity": HOURLY_GRANULARITY,
        }
    )
    payload = _request_json(f"{COINBASE_BASE_URL}/products/{product}/candles?{query}")
    if not isinstance(payload, list):
        raise FourHourDataError(f"Unexpected Coinbase candle response for {product}: {payload}")
    return payload


def fetch_coinbase_hourly(
    product: str,
    start: datetime,
    end_exclusive: datetime,
    *,
    request_pause_seconds: float = 0.12,
) -> list[Candle]:
    start_utc = _utc(start).replace(minute=0, second=0, microsecond=0)
    end_utc = _utc(end_exclusive).replace(minute=0, second=0, microsecond=0)
    if end_utc <= start_utc:
        raise FourHourDataError("End must be after start")
    cursor = start_utc
    by_timestamp: dict[datetime, Candle] = {}
    page_span = timedelta(hours=MAX_CANDLES_PER_REQUEST - 1)
    while cursor < end_utc:
        page_end = min(cursor + page_span, end_utc - timedelta(hours=1))
        rows = _request_hourly_page(product, cursor, page_end)
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                raise FourHourDataError(f"Malformed Coinbase candle for {product}: {row}")
            stamp = datetime.fromtimestamp(int(row[0]), tz=timezone.utc)
            if start_utc <= stamp < end_utc:
                by_timestamp[_naive_utc(stamp)] = Candle(
                    timestamp=_naive_utc(stamp),
                    open=float(row[3]),
                    high=float(row[2]),
                    low=float(row[1]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
        cursor = page_end + timedelta(hours=1)
        if cursor < end_utc and request_pause_seconds > 0:
            time.sleep(request_pause_seconds)
    return [by_timestamp[key] for key in sorted(by_timestamp)]


def _four_hour_bucket(timestamp: datetime) -> datetime:
    clean = _naive_utc(timestamp).replace(minute=0, second=0, microsecond=0)
    return clean.replace(hour=(clean.hour // 4) * 4)


def aggregate_hourly_to_four_hour(hourly: list[Candle]) -> list[Candle]:
    grouped: dict[datetime, dict[datetime, Candle]] = {}
    for candle in hourly:
        bucket = _four_hour_bucket(candle.timestamp)
        grouped.setdefault(bucket, {})[candle.timestamp] = candle
    output: list[Candle] = []
    for bucket in sorted(grouped):
        expected = [bucket + timedelta(hours=offset) for offset in range(4)]
        rows = grouped[bucket]
        if any(stamp not in rows for stamp in expected):
            continue
        candles = [rows[stamp] for stamp in expected]
        output.append(
            Candle(
                timestamp=bucket,
                open=candles[0].open,
                high=max(item.high for item in candles),
                low=min(item.low for item in candles),
                close=candles[-1].close,
                volume=sum(item.volume for item in candles),
            )
        )
    return output


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def fetch_v14_four_hour_bundle(
    out_dir: str | Path,
    start: datetime = EXPECTED_START,
    end_exclusive: datetime = EXPECTED_END_EXCLUSIVE,
) -> FourHourManifest:
    if _naive_utc(start) != EXPECTED_START or _naive_utc(end_exclusive) != EXPECTED_END_EXCLUSIVE:
        raise FourHourDataError("v1.4 fetch interval is frozen")
    root = Path(out_dir)
    hourly_dir = root / "hourly"
    four_hour_dir = root / "four_hour"
    hourly_dir.mkdir(parents=True, exist_ok=True)
    four_hour_dir.mkdir(parents=True, exist_ok=True)
    sources: list[FourHourSource] = []
    histories: dict[str, list[Candle]] = {}
    for symbol, product in SYMBOL_TO_PRODUCT.items():
        hourly = fetch_coinbase_hourly(product, start, end_exclusive)
        four_hour = aggregate_hourly_to_four_hour(hourly)
        if len(hourly) != EXPECTED_HOURLY_BARS:
            raise FourHourDataError(f"{symbol} has {len(hourly)} hourly candles; {EXPECTED_HOURLY_BARS} required")
        if len(four_hour) != EXPECTED_FOUR_HOUR_BARS:
            raise FourHourDataError(f"{symbol} has {len(four_hour)} complete four-hour candles; {EXPECTED_FOUR_HOUR_BARS} required")
        if four_hour[0].timestamp != EXPECTED_START:
            raise FourHourDataError(f"Unexpected first four-hour candle for {symbol}: {four_hour[0].timestamp}")
        expected_last = EXPECTED_END_EXCLUSIVE - timedelta(hours=4)
        if four_hour[-1].timestamp != expected_last:
            raise FourHourDataError(f"Unexpected final four-hour candle for {symbol}: {four_hour[-1].timestamp}")
        hourly_path = save_candles_csv(symbol, hourly, hourly_dir)
        four_hour_path = save_candles_csv(symbol, four_hour, four_hour_dir)
        histories[symbol] = four_hour
        sources.append(
            FourHourSource(
                symbol=symbol,
                product=product,
                hourly_path=_relative(hourly_path, root),
                four_hour_path=_relative(four_hour_path, root),
                hourly_sha256=sha256_file(hourly_path),
                four_hour_sha256=sha256_file(four_hour_path),
                hourly_rows=len(hourly),
                four_hour_rows=len(four_hour),
                first_hour=hourly[0].timestamp.isoformat(),
                last_hour=hourly[-1].timestamp.isoformat(),
                first_four_hour=four_hour[0].timestamp.isoformat(),
                last_four_hour=four_hour[-1].timestamp.isoformat(),
            )
        )
    common = set.intersection(*(set(item.timestamp for item in histories[s]) for s in REQUIRED_SYMBOLS))
    if len(common) != EXPECTED_FOUR_HOUR_BARS:
        raise FourHourDataError(f"Only {len(common)} common four-hour timestamps are available")
    manifest = FourHourManifest(
        schema_version="1.0",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        requested_start=EXPECTED_START.isoformat(),
        requested_end_exclusive=EXPECTED_END_EXCLUSIVE.isoformat(),
        sources=sources,
        dataset_fingerprint=dataset_fingerprint(histories),
    )
    (root / "manifest.json").write_text(json.dumps(asdict(manifest), indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def verify_v14_four_hour_manifest(root: str | Path) -> dict[str, Any]:
    base = Path(root)
    payload = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0" or payload.get("paper_only") is not True:
        raise FourHourDataError("Invalid v1.4 four-hour manifest")
    if payload.get("requested_start") != EXPECTED_START.isoformat():
        raise FourHourDataError("v1.4 market-data start changed")
    if payload.get("requested_end_exclusive") != EXPECTED_END_EXCLUSIVE.isoformat():
        raise FourHourDataError("v1.4 market-data end changed")
    sources = payload.get("sources", [])
    if {item.get("symbol") for item in sources} != set(REQUIRED_SYMBOLS):
        raise FourHourDataError("v1.4 manifest symbol set changed")
    for item in sources:
        hourly = base / item["hourly_path"]
        four_hour = base / item["four_hour_path"]
        if not hourly.is_file() or not four_hour.is_file():
            raise FourHourDataError(f"Missing source file for {item['symbol']}")
        if sha256_file(hourly) != item["hourly_sha256"]:
            raise FourHourDataError(f"Hourly hash mismatch for {item['symbol']}")
        if sha256_file(four_hour) != item["four_hour_sha256"]:
            raise FourHourDataError(f"Four-hour hash mismatch for {item['symbol']}")
        if item["hourly_rows"] != EXPECTED_HOURLY_BARS or item["four_hour_rows"] != EXPECTED_FOUR_HOUR_BARS:
            raise FourHourDataError(f"Unexpected source row count for {item['symbol']}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch the frozen v1.4 Coinbase hourly and four-hour bundle")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    manifest = fetch_v14_four_hour_bundle(args.out)
    print(json.dumps(asdict(manifest), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
