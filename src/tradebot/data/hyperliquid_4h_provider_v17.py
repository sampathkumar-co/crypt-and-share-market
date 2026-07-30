from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from tradebot.backtest.research_gate import dataset_fingerprint, load_histories
from tradebot.data.crypto_external_factors import ExternalFactorDataError
from tradebot.data.crypto_external_factors_hyperliquid_rate_limited import (
    _rate_limited_post_json,
)

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
EXPECTED_START = datetime(2024, 4, 18)
EXPECTED_END_EXCLUSIVE = datetime(2025, 11, 23)
EXPECTED_ROWS = 3_504
INTERVAL = "4h"
FOUR_HOURS = timedelta(hours=4)

SYMBOL_TO_COIN = {
    "APTUSDT": "APT",
    "ARBUSDT": "ARB",
    "AVAXUSDT": "AVAX",
    "DOTUSDT": "DOT",
    "FILUSDT": "FIL",
    "NEARUSDT": "NEAR",
    "OPUSDT": "OP",
    "SUIUSDT": "SUI",
}

_post_json: Callable[[dict[str, Any]], Any] = _rate_limited_post_json


@dataclass(frozen=True)
class HyperliquidPriceSource:
    symbol: str
    coin: str
    provider: str
    endpoint: str
    interval: str
    raw_relative_path: str
    raw_sha256: str
    csv_relative_path: str
    csv_sha256: str
    rows: int
    first_timestamp: str
    last_timestamp: str


@dataclass(frozen=True)
class HyperliquidPriceManifest:
    schema_version: str
    retrieved_at: str
    provider: str
    requested_start: str
    requested_end_exclusive: str
    expected_rows_per_asset: int
    dataset_fingerprint: str
    sources: list[HyperliquidPriceSource]
    paper_only: bool = True


class HyperliquidPriceDataError(ValueError):
    pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_timestamps() -> list[datetime]:
    output = []
    cursor = EXPECTED_START
    while cursor < EXPECTED_END_EXCLUSIVE:
        output.append(cursor)
        cursor += FOUR_HOURS
    if len(output) != EXPECTED_ROWS:
        raise HyperliquidPriceDataError(
            f"Frozen v1.7 grid has {len(output)} timestamps; {EXPECTED_ROWS} required"
        )
    return output


def _parse_rows(symbol: str, rows: Any) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if not isinstance(rows, list):
        raise HyperliquidPriceDataError(
            f"Unexpected Hyperliquid candle response for {symbol}"
        )
    expected = _expected_timestamps()
    expected_set = set(expected)
    parsed: dict[datetime, dict[str, str]] = {}
    raw_filtered: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise HyperliquidPriceDataError(f"Malformed candle row for {symbol}: {row}")
        try:
            stamp = datetime.fromtimestamp(int(row["t"]) / 1000, tz=timezone.utc).replace(tzinfo=None)
            open_value = float(row["o"])
            high_value = float(row["h"])
            low_value = float(row["l"])
            close_value = float(row["c"])
            volume_value = float(row["v"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise HyperliquidPriceDataError(
                f"Malformed candle row for {symbol}: {row}"
            ) from exc
        if stamp not in expected_set:
            continue
        if stamp in parsed:
            raise HyperliquidPriceDataError(
                f"Duplicate Hyperliquid timestamp for {symbol}: {stamp.isoformat()}"
            )
        if min(open_value, high_value, low_value, close_value) <= 0 or volume_value < 0:
            raise HyperliquidPriceDataError(
                f"Invalid Hyperliquid OHLCV for {symbol}: {stamp.isoformat()}"
            )
        if high_value < max(open_value, close_value) or low_value > min(open_value, close_value):
            raise HyperliquidPriceDataError(
                f"Inconsistent Hyperliquid high/low for {symbol}: {stamp.isoformat()}"
            )
        parsed[stamp] = {
            "timestamp": stamp.isoformat(),
            "open": f"{open_value:.16g}",
            "high": f"{high_value:.16g}",
            "low": f"{low_value:.16g}",
            "close": f"{close_value:.16g}",
            "volume": f"{volume_value:.16g}",
        }
        raw_filtered.append(row)

    missing = [stamp for stamp in expected if stamp not in parsed]
    if missing:
        raise HyperliquidPriceDataError(
            f"{symbol} is missing {len(missing)} frozen four-hour timestamps; "
            f"first missing {missing[0].isoformat()}"
        )
    ordered = [parsed[stamp] for stamp in expected]
    if len(ordered) != EXPECTED_ROWS:
        raise HyperliquidPriceDataError(
            f"{symbol} has {len(ordered)} candles; {EXPECTED_ROWS} required"
        )
    return raw_filtered, ordered


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("timestamp", "open", "high", "low", "close", "volume"),
        )
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary.replace(path)


def fetch_hyperliquid_4h_bundle(out_dir: str | Path) -> HyperliquidPriceManifest:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    start_ms = int(EXPECTED_START.replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(EXPECTED_END_EXCLUSIVE.replace(tzinfo=timezone.utc).timestamp() * 1000) - 1
    sources: list[HyperliquidPriceSource] = []

    for symbol, coin in SYMBOL_TO_COIN.items():
        response = _post_json(
            {
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": INTERVAL,
                    "startTime": start_ms,
                    "endTime": end_ms,
                },
            }
        )
        raw_rows, csv_rows = _parse_rows(symbol, response)
        raw_path = root / "raw" / f"{symbol}.json"
        csv_path = root / "four_hour" / f"{symbol}.csv"
        _write_json(raw_path, raw_rows)
        _write_csv(csv_path, csv_rows)
        sources.append(
            HyperliquidPriceSource(
                symbol=symbol,
                coin=coin,
                provider="hyperliquid-public-info",
                endpoint=f"{HYPERLIQUID_INFO_URL}#candleSnapshot:{coin}:4h",
                interval=INTERVAL,
                raw_relative_path=str(raw_path.relative_to(root)),
                raw_sha256=sha256_file(raw_path),
                csv_relative_path=str(csv_path.relative_to(root)),
                csv_sha256=sha256_file(csv_path),
                rows=len(csv_rows),
                first_timestamp=csv_rows[0]["timestamp"],
                last_timestamp=csv_rows[-1]["timestamp"],
            )
        )

    histories = load_histories(root / "four_hour")
    manifest = HyperliquidPriceManifest(
        schema_version="1.0",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        provider="hyperliquid-public-info",
        requested_start=EXPECTED_START.isoformat(),
        requested_end_exclusive=EXPECTED_END_EXCLUSIVE.isoformat(),
        expected_rows_per_asset=EXPECTED_ROWS,
        dataset_fingerprint=dataset_fingerprint(histories),
        sources=sources,
    )
    _write_json(root / "manifest.json", asdict(manifest))
    return manifest


def verify_hyperliquid_4h_manifest(root_dir: str | Path) -> dict[str, Any]:
    root = Path(root_dir)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise HyperliquidPriceDataError("Hyperliquid v1.7 manifest is missing")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("provider") != "hyperliquid-public-info":
        raise HyperliquidPriceDataError("Unexpected Hyperliquid v1.7 provider")
    if payload.get("requested_start") != EXPECTED_START.isoformat():
        raise HyperliquidPriceDataError("Hyperliquid v1.7 start changed")
    if payload.get("requested_end_exclusive") != EXPECTED_END_EXCLUSIVE.isoformat():
        raise HyperliquidPriceDataError("Hyperliquid v1.7 end changed")
    if payload.get("expected_rows_per_asset") != EXPECTED_ROWS:
        raise HyperliquidPriceDataError("Hyperliquid v1.7 row contract changed")
    sources = payload.get("sources")
    if not isinstance(sources, list) or len(sources) != len(SYMBOL_TO_COIN):
        raise HyperliquidPriceDataError("Hyperliquid v1.7 source count changed")
    if {item.get("symbol") for item in sources} != set(SYMBOL_TO_COIN):
        raise HyperliquidPriceDataError("Hyperliquid v1.7 universe changed")
    for item in sources:
        raw_path = root / item["raw_relative_path"]
        csv_path = root / item["csv_relative_path"]
        if sha256_file(raw_path) != item.get("raw_sha256"):
            raise HyperliquidPriceDataError(
                f"Raw Hyperliquid hash changed for {item.get('symbol')}"
            )
        if sha256_file(csv_path) != item.get("csv_sha256"):
            raise HyperliquidPriceDataError(
                f"CSV Hyperliquid hash changed for {item.get('symbol')}"
            )
        if item.get("rows") != EXPECTED_ROWS:
            raise HyperliquidPriceDataError(
                f"Hyperliquid row count changed for {item.get('symbol')}"
            )
        if item.get("first_timestamp") != EXPECTED_START.isoformat():
            raise HyperliquidPriceDataError(
                f"Hyperliquid first timestamp changed for {item.get('symbol')}"
            )
        if item.get("last_timestamp") != (
            EXPECTED_END_EXCLUSIVE - FOUR_HOURS
        ).isoformat():
            raise HyperliquidPriceDataError(
                f"Hyperliquid last timestamp changed for {item.get('symbol')}"
            )
    histories = load_histories(root / "four_hour")
    if set(histories) != set(SYMBOL_TO_COIN):
        raise HyperliquidPriceDataError("Hyperliquid CSV universe changed")
    if any(len(candles) != EXPECTED_ROWS for candles in histories.values()):
        raise HyperliquidPriceDataError("Hyperliquid CSV rows changed")
    fingerprint = dataset_fingerprint(histories)
    if fingerprint != payload.get("dataset_fingerprint"):
        raise HyperliquidPriceDataError("Hyperliquid dataset fingerprint changed")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch frozen v1.7.1 Hyperliquid four-hour price data"
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        manifest = fetch_hyperliquid_4h_bundle(args.out)
    except (ExternalFactorDataError, HyperliquidPriceDataError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(asdict(manifest), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
