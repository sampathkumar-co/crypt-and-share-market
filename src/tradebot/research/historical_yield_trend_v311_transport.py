from __future__ import annotations

import csv
import hashlib
import io
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tradebot.research import historical_yield_trend_v31 as v31

CASH_SOURCE_POLICY = (
    "federal_reserve_h15_3_month_constant_maturity_exact_series"
)
CASH_TRANSPORT_POLICY = (
    "fred_once_each_then_federal_reserve_h15_package_twice"
)
FRED_FALLBACK_URL = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO"
)
FED_H15_URL = (
    "https://www.federalreserve.gov/datadownload/Output.aspx?"
    "filetype=csv&from=&label=include&lastobs=&layout=seriescolumn&"
    "rel=H15&series=bf17364827e38702b42a58cf8eaa3f78&to=&type=package"
)
FED_H15_SERIES = "H15/H15/RIFLGFCM03_N.B"
FED_H15_COLUMN = "RIFLGFCM03_N.B"
RANGE_START = datetime(2017, 8, 31, tzinfo=timezone.utc)
RANGE_END = datetime(2025, 12, 31, tzinfo=timezone.utc)
MIN_OBSERVATIONS = 2_000

TRANSPORT_AUDIT: dict[str, Any] = {}


class CashTransportError(v31.HistoricalYieldTrendV31Error):
    """Raised when no authoritative H.15 cash-rate source is usable."""


def reset_transport_audit() -> None:
    TRANSPORT_AUDIT.clear()
    TRANSPORT_AUDIT.update(
        {
            "attempt_count": 0,
            "attempts": [],
            "attempted_urls": [],
            "selected_url": None,
            "selected_source": None,
            "series_id": FED_H15_SERIES,
            "raw_sha256": None,
            "normalized_sha256": None,
            "observation_count": 0,
            "first_date": None,
            "last_date": None,
        }
    )


def _record_attempt(url: str, source: str, status: str, detail: str = "") -> None:
    TRANSPORT_AUDIT["attempt_count"] += 1
    if url not in TRANSPORT_AUDIT["attempted_urls"]:
        TRANSPORT_AUDIT["attempted_urls"].append(url)
    item = {"url": url, "source": source, "status": status}
    if detail:
        item["detail"] = detail[:300]
    TRANSPORT_AUDIT["attempts"].append(item)


def _download(url: str, timeout: float, source: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "tradebot-v31.1-h15-transport/1.0",
            "Accept": "text/csv,*/*;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            if response.status != 200:
                raise CashTransportError(
                    f"{source} returned HTTP {response.status}"
                )
            content = response.read()
        if not content:
            raise CashTransportError(f"{source} returned an empty response")
        _record_attempt(url, source, "success")
        return content
    except (HTTPError, URLError, TimeoutError, CashTransportError) as exc:
        _record_attempt(url, source, "failure", str(exc))
        raise CashTransportError(f"{source} download failed: {exc}") from exc


def _parse_decimal(raw: str, row_name: str) -> float:
    try:
        return float(Decimal(raw) / Decimal("100"))
    except (InvalidOperation, ValueError) as exc:
        raise CashTransportError(f"invalid rate in {row_name}: {raw!r}") from exc


def parse_fred_rates(content: bytes) -> dict[datetime, float]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CashTransportError("FRED CSV is not UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text))
    fields = list(reader.fieldnames or [])
    date_column = (
        "observation_date"
        if "observation_date" in fields
        else "DATE"
        if "DATE" in fields
        else None
    )
    if date_column is None or "DGS3MO" not in fields:
        raise CashTransportError(f"FRED columns unavailable: {fields}")
    rates: dict[datetime, float] = {}
    for row in reader:
        raw = str(row.get("DGS3MO", "")).strip()
        if not raw or raw == ".":
            continue
        try:
            day = datetime.fromisoformat(str(row[date_column])).replace(
                tzinfo=timezone.utc
            )
        except (TypeError, ValueError) as exc:
            raise CashTransportError(f"invalid FRED date: {row}") from exc
        if day in rates:
            raise CashTransportError(f"duplicate FRED date: {day.date()}")
        rates[day] = _parse_decimal(raw, f"FRED {day.date()}")
    return rates


def parse_federal_reserve_h15_rates(content: bytes) -> dict[datetime, float]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CashTransportError("Federal Reserve H.15 CSV is not UTF-8") from exc
    rows = list(csv.reader(io.StringIO(text)))
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if row and row[0].strip() == "Time Period"
        ),
        None,
    )
    if header_index is None:
        raise CashTransportError("Federal Reserve H.15 data header unavailable")
    header = rows[header_index]
    try:
        date_index = header.index("Time Period")
        rate_index = header.index(FED_H15_COLUMN)
    except ValueError as exc:
        raise CashTransportError(
            f"Federal Reserve H.15 series {FED_H15_COLUMN} unavailable"
        ) from exc
    rates: dict[datetime, float] = {}
    for row in rows[header_index + 1 :]:
        if len(row) <= max(date_index, rate_index):
            continue
        raw_date = row[date_index].strip()
        raw_rate = row[rate_index].strip()
        if not raw_date or not raw_rate or raw_rate in {"ND", "."}:
            continue
        try:
            day = datetime.fromisoformat(raw_date).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise CashTransportError(
                f"invalid Federal Reserve H.15 date: {raw_date!r}"
            ) from exc
        if not RANGE_START <= day <= RANGE_END:
            continue
        if day in rates:
            raise CashTransportError(
                f"duplicate Federal Reserve H.15 date: {day.date()}"
            )
        rates[day] = _parse_decimal(
            raw_rate,
            f"Federal Reserve H.15 {day.date()}",
        )
    return rates


def _validate_rates(rates: dict[datetime, float], source: str) -> None:
    if len(rates) < MIN_OBSERVATIONS:
        raise CashTransportError(
            f"{source} has only {len(rates)} observations; "
            f"minimum is {MIN_OBSERVATIONS}"
        )
    dates = sorted(rates)
    if dates[0] != RANGE_START:
        raise CashTransportError(
            f"{source} first date is {dates[0].date()}, expected {RANGE_START.date()}"
        )
    if dates[-1] != RANGE_END:
        raise CashTransportError(
            f"{source} last date is {dates[-1].date()}, expected {RANGE_END.date()}"
        )
    for day, value in rates.items():
        if not -0.10 < value < 0.30:
            raise CashTransportError(
                f"{source} invalid annual rate on {day.date()}: {value}"
            )


def normalize_rates(rates: dict[datetime, float]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["observation_date", "DGS3MO"])
    for day in sorted(rates):
        percent = Decimal(str(rates[day])) * Decimal("100")
        writer.writerow([day.date().isoformat(), format(percent, "f")])
    return output.getvalue().encode("utf-8")


def _finish(
    raw: bytes,
    rates: dict[datetime, float],
    source: str,
    url: str,
) -> tuple[bytes, dict[str, str]]:
    _validate_rates(rates, source)
    normalized = normalize_rates(rates)
    dates = sorted(rates)
    TRANSPORT_AUDIT.update(
        {
            "selected_url": url,
            "selected_source": source,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "normalized_sha256": hashlib.sha256(normalized).hexdigest(),
            "observation_count": len(rates),
            "first_date": dates[0].date().isoformat(),
            "last_date": dates[-1].date().isoformat(),
        }
    )
    return normalized, {
        "key": "cash:DGS3MO",
        "url": url,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def download_cash_series_with_resilience(
    *,
    downloader: Callable[[str, float, str], bytes] = _download,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[bytes, dict[str, str]]:
    reset_transport_audit()
    fred_sources = (
        (v31.FRED_URL, "fred_dated_dgs3mo"),
        (FRED_FALLBACK_URL, "fred_full_dgs3mo"),
    )
    for url, source in fred_sources:
        try:
            raw = downloader(url, 15.0, source)
            return _finish(raw, parse_fred_rates(raw), source, url)
        except CashTransportError:
            continue

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            raw = downloader(FED_H15_URL, 30.0, "federal_reserve_h15")
            rates = parse_federal_reserve_h15_rates(raw)
            return _finish(raw, rates, "federal_reserve_h15", FED_H15_URL)
        except CashTransportError as exc:
            last_error = exc
            if attempt == 0:
                sleeper(1.0)
    raise CashTransportError(
        f"all authoritative H.15 transports failed: {last_error}"
    )
