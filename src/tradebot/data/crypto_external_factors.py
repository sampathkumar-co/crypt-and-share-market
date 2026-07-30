from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

COINMETRICS_BASE = "https://raw.githubusercontent.com/coinmetrics/data/master/csv"
BYBIT_FUNDING_URL = "https://api.bybit.com/v5/market/funding/history"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

COINMETRICS_ASSETS = {
    "LTCUSDT": "ltc",
    "BCHUSDT": "bch",
    "LINKUSDT": "link",
    "XLMUSDT": "xlm",
    "ETCUSDT": "etc",
    "ATOMUSDT": "atom",
    "UNIUSDT": "uni",
    "AAVEUSDT": "aave",
}
FRED_SERIES = ("VIXCLS", "DTWEXBGS", "DGS10")


class ExternalFactorDataError(RuntimeError):
    """Raised when read-only external factor data is incomplete or malformed."""


@dataclass(frozen=True)
class SourceFile:
    provider: str
    source_id: str
    url: str
    relative_path: str
    sha256: str
    bytes: int
    rows: int
    first_date: str | None
    last_date: str | None
    missing_values: int


@dataclass(frozen=True)
class ExternalFactorManifest:
    schema_version: str
    retrieved_at: str
    requested_start: str
    requested_end: str
    files: list[SourceFile]
    licences: dict[str, str]
    paper_only: bool = True


def _request_bytes(url: str, *, timeout: float = 45.0, retries: int = 4) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": "application/json,text/csv,*/*",
            "User-Agent": "crypt-and-share-market-paper-research/1.0",
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed public data URLs
                if response.status != 200:
                    raise ExternalFactorDataError(f"HTTP {response.status} for {url}")
                return response.read()
        except (HTTPError, URLError, TimeoutError, ExternalFactorDataError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(float(attempt))
    raise ExternalFactorDataError(f"Failed to fetch {url}: {last_error}")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_date(value: str) -> date:
    clean = value.strip()
    if not clean:
        raise ValueError("empty date")
    return date.fromisoformat(clean[:10])


def _float_or_none(value: str | None) -> float | None:
    if value is None:
        return None
    clean = value.strip()
    if clean in {"", ".", "NA", "null", "None"}:
        return None
    try:
        return float(clean)
    except ValueError:
        return None


def _write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> tuple[int, int, str | None, str | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    missing = 0
    first: str | None = None
    last: str | None = None
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            rendered = {key: row.get(key, "") for key in fieldnames}
            writer.writerow(rendered)
            row_date = str(rendered[fieldnames[0]])
            first = first or row_date
            last = row_date
            missing += sum(value in (None, "") for key, value in rendered.items() if key != fieldnames[0])
            count += 1
    return count, missing, first, last


def fetch_coinmetrics_filtered(
    asset: str,
    metrics: tuple[str, ...],
    start: date,
    end: date,
    destination: Path,
) -> SourceFile:
    url = f"{COINMETRICS_BASE}/{asset}.csv"
    payload = _request_bytes(url)
    text = payload.decode("utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames:
        raise ExternalFactorDataError(f"Coin Metrics {asset} CSV has no header")
    date_column = reader.fieldnames[0]
    available = [metric for metric in metrics if metric in reader.fieldnames]
    if not available:
        raise ExternalFactorDataError(f"Coin Metrics {asset} has none of {metrics}")
    rows: list[dict[str, Any]] = []
    for source in reader:
        try:
            row_date = _parse_date(source.get(date_column, ""))
        except ValueError:
            continue
        if start <= row_date <= end:
            row: dict[str, Any] = {"date": row_date.isoformat()}
            for metric in available:
                value = _float_or_none(source.get(metric))
                row[metric] = "" if value is None else f"{value:.16g}"
            rows.append(row)
    if not rows:
        raise ExternalFactorDataError(f"Coin Metrics {asset} has no rows in requested interval")
    fieldnames = ["date", *available]
    count, missing, first, last = _write_csv(destination, fieldnames, rows)
    return SourceFile(
        provider="coinmetrics-community-archive",
        source_id=asset,
        url=url,
        relative_path=destination.as_posix(),
        sha256=sha256_file(destination),
        bytes=destination.stat().st_size,
        rows=count,
        first_date=first,
        last_date=last,
        missing_values=missing,
    )


def _fetch_bybit_page(symbol: str, end_ms: int) -> list[dict[str, Any]]:
    query = urlencode({"category": "linear", "symbol": symbol, "endTime": end_ms, "limit": 200})
    payload = json.loads(_request_bytes(f"{BYBIT_FUNDING_URL}?{query}").decode("utf-8"))
    if payload.get("retCode") != 0:
        raise ExternalFactorDataError(f"Bybit funding error for {symbol}: {payload.get('retMsg')}")
    rows = payload.get("result", {}).get("list", [])
    if not isinstance(rows, list):
        raise ExternalFactorDataError(f"Unexpected Bybit funding response for {symbol}")
    return rows


def fetch_bybit_funding(symbol: str, start: date, end: date, destination: Path) -> SourceFile:
    start_ms = int(datetime.combine(start, datetime_time.min, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.combine(end + timedelta(days=1), datetime_time.min, tzinfo=timezone.utc).timestamp() * 1000) - 1
    cursor = end_ms
    records: dict[int, float] = {}
    for _ in range(30):
        rows = _fetch_bybit_page(symbol, cursor)
        if not rows:
            break
        stamps: list[int] = []
        for row in rows:
            stamp = int(row["fundingRateTimestamp"])
            stamps.append(stamp)
            if start_ms <= stamp <= end_ms:
                records[stamp] = float(row["fundingRate"])
        earliest = min(stamps)
        if earliest <= start_ms:
            break
        if earliest >= cursor:
            raise ExternalFactorDataError(f"Bybit pagination did not advance for {symbol}")
        cursor = earliest - 1
        time.sleep(0.05)
    if not records:
        raise ExternalFactorDataError(f"No Bybit funding records for {symbol}")
    rows = [
        {
            "timestamp": datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).isoformat(),
            "funding_rate": f"{records[stamp]:.16g}",
        }
        for stamp in sorted(records)
    ]
    count, missing, first, last = _write_csv(destination, ["timestamp", "funding_rate"], rows)
    return SourceFile(
        provider="bybit-public-market",
        source_id=symbol,
        url=f"{BYBIT_FUNDING_URL}?category=linear&symbol={symbol}",
        relative_path=destination.as_posix(),
        sha256=sha256_file(destination),
        bytes=destination.stat().st_size,
        rows=count,
        first_date=first[:10] if first else None,
        last_date=last[:10] if last else None,
        missing_values=missing,
    )


def fetch_fred_series(series: str, start: date, end: date, destination: Path) -> SourceFile:
    url = f"{FRED_CSV_URL}?{urlencode({'id': series})}"
    payload = _request_bytes(url)
    text = payload.decode("utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames or len(reader.fieldnames) < 2:
        raise ExternalFactorDataError(f"FRED {series} CSV has no value column")
    date_column = reader.fieldnames[0]
    value_column = reader.fieldnames[1]
    rows: list[dict[str, Any]] = []
    for source in reader:
        try:
            row_date = _parse_date(source.get(date_column, ""))
        except ValueError:
            continue
        value = _float_or_none(source.get(value_column))
        if start <= row_date <= end and value is not None:
            rows.append({"date": row_date.isoformat(), "value": f"{value:.16g}"})
    if not rows:
        raise ExternalFactorDataError(f"FRED {series} has no rows in requested interval")
    count, missing, first, last = _write_csv(destination, ["date", "value"], rows)
    return SourceFile(
        provider="fred-public-csv",
        source_id=series,
        url=url,
        relative_path=destination.as_posix(),
        sha256=sha256_file(destination),
        bytes=destination.stat().st_size,
        rows=count,
        first_date=first,
        last_date=last,
        missing_values=missing,
    )


def fetch_external_factor_bundle(out_dir: str | Path, start: date, end: date) -> ExternalFactorManifest:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    files: list[SourceFile] = []
    onchain_metrics = ("AdrActCnt", "TxCnt")
    for symbol, asset in COINMETRICS_ASSETS.items():
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
            # Missing asset-level Community coverage is represented explicitly in the manifest.
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
    for symbol in COINMETRICS_ASSETS:
        files.append(fetch_bybit_funding(symbol, start, end, root / "bybit" / f"{symbol}.csv"))
    for series in FRED_SERIES:
        files.append(fetch_fred_series(series, start, end, root / "fred" / f"{series}.csv"))

    relative_files = [
        SourceFile(**{**asdict(item), "relative_path": str(Path(item.relative_path).relative_to(root))})
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
            "bybit-public-market": "Bybit public API terms",
            "fred-public-csv": "Federal Reserve Bank of St. Louis data terms",
        },
    )
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(asdict(manifest), indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def verify_external_manifest(root: str | Path) -> dict[str, Any]:
    base = Path(root)
    path = base / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0" or payload.get("paper_only") is not True:
        raise ExternalFactorDataError("Invalid external-factor manifest")
    for item in payload.get("files", []):
        source = base / item["relative_path"]
        if not source.is_file():
            raise ExternalFactorDataError(f"Missing external source file: {source}")
        if sha256_file(source) != item["sha256"]:
            raise ExternalFactorDataError(f"External source hash mismatch: {source}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch read-only crypto external-factor data")
    parser.add_argument("--out", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args(argv)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        raise SystemExit("--end must not precede --start")
    manifest = fetch_external_factor_bundle(args.out, start, end)
    print(json.dumps(asdict(manifest), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
