from __future__ import annotations

import csv
import hashlib
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import historical_coinbase_replication_v32 as v32
from tradebot.research import historical_yield_trend_v31 as v31
from tradebot.research import historical_yield_trend_v311_transport as cash_transport

HISTORY_DAYS = 260


class ForwardSourceV33Error(RuntimeError):
    pass


def day_floor(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def latest_completed_day(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ForwardSourceV33Error("as-of time must be timezone-aware")
    return day_floor(now) - timedelta(days=1)


def _download(url: str, timeout: float = 30.0) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "tradebot-v33-forward-observation/1.0",
            "Accept": "application/json,text/csv,*/*;q=0.5",
        },
    )
    last_error: Exception | None = None
    for _ in range(3):
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                if response.status != 200:
                    raise ForwardSourceV33Error(
                        f"source returned HTTP {response.status}"
                    )
                content = response.read()
            if not content:
                raise ForwardSourceV33Error("source returned empty content")
            return content
        except (HTTPError, URLError, TimeoutError, ForwardSourceV33Error) as exc:
            last_error = exc
    raise ForwardSourceV33Error(f"source download failed: {last_error}")


def _bar_hash(bars: dict[datetime, Any]) -> str:
    rows = [
        {
            "date": day.date().isoformat(),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "quote_volume": bar.quote_volume,
        }
        for day, bar in sorted(bars.items())
    ]
    return hashlib.sha256(canonical_json(rows).encode()).hexdigest()


def fetch_coinbase_history(completed_day: datetime):
    start = completed_day - timedelta(days=HISTORY_DAYS - 1)
    bars: dict[str, dict[datetime, Any]] = {asset: {} for asset in v31.ASSETS}
    manifests: list[dict[str, Any]] = []
    normalized: dict[str, str] = {}
    for asset in v31.ASSETS:
        product = v32.PRODUCTS[asset]
        url = v32._candle_url(product, start, completed_day)
        raw = _download(url)
        parsed = v32._parse_coinbase_candles(
            raw,
            asset=asset,
            requested_start=start,
            requested_end=completed_day,
        )
        days = [start + timedelta(days=index) for index in range(HISTORY_DAYS)]
        missing = [day for day in days if day not in parsed]
        if missing:
            raise ForwardSourceV33Error(
                f"Coinbase {asset} missing completed day {missing[0].date()}"
            )
        bars[asset] = {day: parsed[day] for day in days}
        normalized[asset] = _bar_hash(bars[asset])
        manifests.append(
            {
                "key": f"coinbase:{asset}",
                "provider": "coinbase-exchange-public-rest",
                "product": product,
                "url": url,
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "normalized_sha256": normalized[asset],
                "rows": HISTORY_DAYS,
                "first_date": start.date().isoformat(),
                "last_date": completed_day.date().isoformat(),
            }
        )
    return bars, manifests, normalized


def _parse_h15(raw: bytes, cutoff: datetime) -> dict[datetime, float]:
    try:
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    except UnicodeDecodeError as exc:
        raise ForwardSourceV33Error("H.15 package is not UTF-8") from exc
    header_index = next(
        (i for i, row in enumerate(rows) if row and row[0] == "Time Period"),
        None,
    )
    if header_index is None:
        raise ForwardSourceV33Error("H.15 data header unavailable")
    header = rows[header_index]
    try:
        date_index = header.index("Time Period")
        rate_index = header.index(cash_transport.FED_H15_COLUMN)
    except ValueError as exc:
        raise ForwardSourceV33Error("exact H.15 series unavailable") from exc
    rates: dict[datetime, float] = {}
    for row in rows[header_index + 1 :]:
        if len(row) <= max(date_index, rate_index):
            continue
        raw_date, raw_rate = row[date_index].strip(), row[rate_index].strip()
        if not raw_date or not raw_rate or raw_rate in {"ND", "."}:
            continue
        try:
            day = datetime.fromisoformat(raw_date).replace(tzinfo=timezone.utc)
            rate = float(Decimal(raw_rate) / Decimal("100"))
        except (ValueError, InvalidOperation) as exc:
            raise ForwardSourceV33Error(f"invalid H.15 row: {row!r}") from exc
        if day <= cutoff:
            if day in rates:
                raise ForwardSourceV33Error(f"duplicate H.15 date {day.date()}")
            rates[day] = rate
    if not rates:
        raise ForwardSourceV33Error("no H.15 observations known by cutoff")
    latest = max(rates)
    if (cutoff - latest).days > 10:
        raise ForwardSourceV33Error(f"stale H.15 rate {latest.date()}")
    return rates


def fetch_h15_evidence(completed_day: datetime):
    raw = _download(cash_transport.FED_H15_URL)
    rates = _parse_h15(raw, completed_day)
    rows = [
        {"date": day.date().isoformat(), "annual_rate": rates[day]}
        for day in sorted(rates)
    ]
    normalized_sha = hashlib.sha256(canonical_json(rows).encode()).hexdigest()
    latest = max(rates)
    evidence = {
        "series_id": cash_transport.FED_H15_SERIES,
        "normalized_sha256": normalized_sha,
        "observation_count": len(rates),
        "latest_known_date": latest.date().isoformat(),
        "latest_known_annual_rate": rates[latest],
    }
    manifest = {
        "key": "cash:DGS3MO",
        "provider": "federal_reserve_h15",
        "url": cash_transport.FED_H15_URL,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        **evidence,
    }
    return evidence, manifest
