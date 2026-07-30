from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tradebot.data.crypto_external_factors import (
    COINMETRICS_ASSETS,
    FRED_SERIES,
    ExternalFactorDataError,
    ExternalFactorManifest,
    SourceFile,
    _write_csv,
    fetch_coinmetrics_filtered,
    fetch_fred_series,
    sha256_file,
)

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
HYPERLIQUID_COINS = {
    "LTCUSDT": "LTC",
    "BCHUSDT": "BCH",
    "LINKUSDT": "LINK",
    "XLMUSDT": "XLM",
    "ETCUSDT": "ETC",
    "ATOMUSDT": "ATOM",
    "UNIUSDT": "UNI",
    "AAVEUSDT": "AAVE",
}


def _post_json(body: dict[str, Any], *, timeout: float = 45.0, retries: int = 4) -> Any:
    payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = Request(
        HYPERLIQUID_INFO_URL,
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "crypt-and-share-market-paper-research/1.0",
        },
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed public API URL
                if response.status != 200:
                    raise ExternalFactorDataError(
                        f"Hyperliquid returned HTTP {response.status}"
                    )
                return json.loads(response.read().decode("utf-8"))
        except (
            HTTPError,
            URLError,
            TimeoutError,
            json.JSONDecodeError,
            ExternalFactorDataError,
        ) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(float(attempt))
    raise ExternalFactorDataError(f"Hyperliquid request failed: {last_error}")


def fetch_hyperliquid_funding(
    symbol: str,
    start: date,
    end: date,
    destination: Path,
) -> SourceFile:
    coin = HYPERLIQUID_COINS.get(symbol)
    if coin is None:
        raise ExternalFactorDataError(f"No Hyperliquid coin mapping for {symbol}")
    if end < start:
        raise ExternalFactorDataError("Funding end date precedes start date")

    start_ms = int(
        datetime.combine(start, datetime_time.min, tzinfo=timezone.utc).timestamp()
        * 1000
    )
    end_ms = int(
        datetime.combine(
            end + timedelta(days=1), datetime_time.min, tzinfo=timezone.utc
        ).timestamp()
        * 1000
    ) - 1
    cursor = start_ms
    records: dict[int, float] = {}

    for _ in range(80):
        rows = _post_json(
            {
                "type": "fundingHistory",
                "coin": coin,
                "startTime": cursor,
                "endTime": end_ms,
            }
        )
        if not isinstance(rows, list):
            raise ExternalFactorDataError(
                f"Unexpected Hyperliquid funding response for {symbol}"
            )
        if not rows:
            break

        stamps: list[int] = []
        for row in rows:
            try:
                stamp = int(row["time"])
                rate = float(row["fundingRate"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ExternalFactorDataError(
                    f"Malformed Hyperliquid funding row for {symbol}: {row}"
                ) from exc
            stamps.append(stamp)
            if start_ms <= stamp <= end_ms:
                records[stamp] = rate

        newest = max(stamps)
        if newest < cursor:
            raise ExternalFactorDataError(
                f"Hyperliquid pagination did not advance for {symbol}"
            )
        if newest >= end_ms:
            break
        next_cursor = newest + 1
        if next_cursor <= cursor:
            raise ExternalFactorDataError(
                f"Hyperliquid pagination stalled for {symbol}"
            )
        cursor = next_cursor
        if len(rows) < 500:
            break
        time.sleep(0.03)

    if not records:
        raise ExternalFactorDataError(
            f"No Hyperliquid funding records for {symbol}"
        )

    first_stamp = min(records)
    last_stamp = max(records)
    first_date = datetime.fromtimestamp(
        first_stamp / 1000, tz=timezone.utc
    ).date()
    last_date = datetime.fromtimestamp(last_stamp / 1000, tz=timezone.utc).date()
    if first_date > start or last_date < end:
        raise ExternalFactorDataError(
            f"Incomplete Hyperliquid funding interval for {symbol}: "
            f"{first_date} to {last_date}"
        )

    rendered = [
        {
            "timestamp": datetime.fromtimestamp(
                stamp / 1000, tz=timezone.utc
            ).isoformat(),
            "funding_rate": f"{records[stamp]:.16g}",
        }
        for stamp in sorted(records)
    ]
    count, missing, first, last = _write_csv(
        destination, ["timestamp", "funding_rate"], rendered
    )
    return SourceFile(
        provider="hyperliquid-public-info",
        source_id=symbol,
        url=f"{HYPERLIQUID_INFO_URL}#fundingHistory:{coin}",
        relative_path=destination.as_posix(),
        sha256=sha256_file(destination),
        bytes=destination.stat().st_size,
        rows=count,
        first_date=first[:10] if first else None,
        last_date=last[:10] if last else None,
        missing_values=missing,
    )


def fetch_external_factor_bundle_hyperliquid(
    out_dir: str | Path,
    start: date,
    end: date,
) -> ExternalFactorManifest:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    files: list[SourceFile] = []
    onchain_metrics = ("AdrActCnt", "TxCnt")

    for _, asset in COINMETRICS_ASSETS.items():
        try:
            files.append(
                fetch_coinmetrics_filtered(
                    asset,
                    onchain_metrics,
                    start,
                    end,
                    root / "coinmetrics" / f"{asset}.csv",
                )
            )
        except ExternalFactorDataError:
            # Missing Community coverage is explicitly neutral in the frozen model.
            continue

    for stablecoin in ("usdt", "usdc"):
        files.append(
            fetch_coinmetrics_filtered(
                stablecoin,
                ("CapMrktCurUSD",),
                start,
                end,
                root / "coinmetrics" / f"{stablecoin}.csv",
            )
        )

    for symbol in HYPERLIQUID_COINS:
        # The legacy directory name is intentionally retained so the frozen v1.3
        # evaluator remains byte-for-byte unchanged. Provider identity and hashes
        # in the manifest unambiguously record Hyperliquid as the source.
        files.append(
            fetch_hyperliquid_funding(
                symbol,
                start,
                end,
                root / "bybit" / f"{symbol}.csv",
            )
        )

    for series in FRED_SERIES:
        files.append(
            fetch_fred_series(
                series, start, end, root / "fred" / f"{series}.csv"
            )
        )

    relative_files = [
        SourceFile(
            **{
                **asdict(item),
                "relative_path": str(
                    Path(item.relative_path).relative_to(root)
                ),
            }
        )
        for item in files
    ]
    manifest = ExternalFactorManifest(
        schema_version="1.0",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        requested_start=start.isoformat(),
        requested_end=end.isoformat(),
        files=relative_files,
        licences={
            "coinmetrics-community-archive": "CC BY-NC 4.0",
            "hyperliquid-public-info": "Hyperliquid public API terms",
            "fred-public-csv": "Federal Reserve Bank of St. Louis data terms",
        },
    )
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(asdict(manifest), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch read-only crypto external factors using Hyperliquid funding"
    )
    parser.add_argument("--out", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args(argv)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        raise SystemExit("--end must not precede --start")
    manifest = fetch_external_factor_bundle_hyperliquid(args.out, start, end)
    print(json.dumps(asdict(manifest), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
