from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SCHEMA_VERSION = "2.0"
COINBASE_BASE = "https://api.exchange.coinbase.com"
HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"
COINMETRICS_BASE = "https://raw.githubusercontent.com/coinmetrics/data/master/csv"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

ASSET_PRODUCTS = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "AVAX": "AVAX-USD",
    "LINK": "LINK-USD",
    "DOGE": "DOGE-USD",
}
FRED_SERIES = ("VIXCLS", "DTWEXBGS", "DGS10")
COINMETRICS_ASSETS = ("usdt", "usdc")
COINMETRICS_METRICS = ("CapMrktCurUSD", "TxTfrValAdjUSD")


class ForwardDataError(RuntimeError):
    """Raised when a forward public-data snapshot violates the frozen contract."""


class HTTPClient(Protocol):
    def json_request(
        self,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> tuple[Any, bytes]: ...

    def bytes_request(self, url: str) -> bytes: ...


@dataclass(frozen=True)
class BookMetrics:
    best_bid: float
    best_ask: float
    mid: float
    spread_bps: float
    bid_notional: float
    ask_notional: float
    imbalance: float


@dataclass(frozen=True)
class SourceEntry:
    source: str
    request: dict[str, Any]
    raw_path: str
    sha256: str


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _float(value: Any, field: str, *, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ForwardDataError(f"{field} is not numeric: {value!r}") from exc
    if not math.isfinite(number):
        raise ForwardDataError(f"{field} is not finite")
    if positive and number <= 0:
        raise ForwardDataError(f"{field} must be positive")
    return number


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


class PublicHTTPClient:
    """Small read-only HTTP client with bounded retries and deterministic bytes."""

    def __init__(self, timeout: float = 20.0, retries: int = 3, backoff_seconds: float = 1.0):
        self.timeout = timeout
        self.retries = retries
        self.backoff_seconds = backoff_seconds

    def json_request(
        self,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> tuple[Any, bytes]:
        body = None if payload is None else canonical_json(payload).encode("utf-8")
        headers = {"Accept": "application/json", "User-Agent": "dual-market-ai-bot/2.0"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        raw = self._request(url, body=body, headers=headers)
        try:
            return json.loads(raw.decode("utf-8")), raw
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ForwardDataError(f"Invalid JSON from {url}") from exc

    def bytes_request(self, url: str) -> bytes:
        return self._request(
            url,
            body=None,
            headers={"Accept": "text/csv,*/*", "User-Agent": "dual-market-ai-bot/2.0"},
        )

    def _request(self, url: str, *, body: bytes | None, headers: dict[str, str]) -> bytes:
        request = Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - fixed public URLs
                    if response.status != 200:
                        raise ForwardDataError(f"HTTP {response.status} from {url}")
                    return response.read()
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {429, 500, 502, 503, 504}:
                    break
            except (URLError, TimeoutError, ForwardDataError) as exc:
                last_error = exc
            if attempt < self.retries:
                time.sleep(self.backoff_seconds * attempt)
        raise ForwardDataError(f"Public request failed after {self.retries} attempts: {url}: {last_error}")


def _book_levels(rows: Any, side: str, depth: int) -> list[tuple[float, float]]:
    if not isinstance(rows, list) or not rows:
        raise ForwardDataError(f"{side} book is empty")
    levels: list[tuple[float, float]] = []
    for index, row in enumerate(rows[:depth]):
        if isinstance(row, dict):
            price, size = row.get("px"), row.get("sz")
        elif isinstance(row, list) and len(row) >= 2:
            price, size = row[0], row[1]
        else:
            raise ForwardDataError(f"Malformed {side} level {index}")
        levels.append((_float(price, f"{side}.price", positive=True), _float(size, f"{side}.size")))
    if any(size < 0 for _, size in levels):
        raise ForwardDataError(f"{side} book contains negative size")
    return levels


def order_book_metrics(bids: Any, asks: Any, depth: int = 10) -> BookMetrics:
    bid_levels = _book_levels(bids, "bid", depth)
    ask_levels = _book_levels(asks, "ask", depth)
    best_bid = max(price for price, _ in bid_levels)
    best_ask = min(price for price, _ in ask_levels)
    if best_bid >= best_ask:
        raise ForwardDataError(f"Crossed book: bid={best_bid}, ask={best_ask}")
    mid = (best_bid + best_ask) / 2.0
    bid_notional = sum(price * size for price, size in bid_levels)
    ask_notional = sum(price * size for price, size in ask_levels)
    total = bid_notional + ask_notional
    imbalance = 0.0 if total == 0 else (bid_notional - ask_notional) / total
    return BookMetrics(
        best_bid=best_bid,
        best_ask=best_ask,
        mid=mid,
        spread_bps=(best_ask - best_bid) / mid * 10_000.0,
        bid_notional=bid_notional,
        ask_notional=ask_notional,
        imbalance=imbalance,
    )


def coinbase_trade_metrics(rows: Any) -> dict[str, float | int]:
    if not isinstance(rows, list):
        raise ForwardDataError("Coinbase trades must be a list")
    maker_buy = maker_sell = 0.0
    for row in rows:
        if not isinstance(row, dict):
            raise ForwardDataError("Malformed Coinbase trade")
        size = _float(row.get("size"), "trade.size")
        if size < 0:
            raise ForwardDataError("Coinbase trade size is negative")
        notional = _float(row.get("price"), "trade.price", positive=True) * size
        side = str(row.get("side", "")).lower()
        if side == "buy":
            maker_buy += notional
        elif side == "sell":
            maker_sell += notional
        else:
            raise ForwardDataError(f"Unknown Coinbase maker side: {side}")
    taker_buy = maker_sell
    taker_sell = maker_buy
    total = taker_buy + taker_sell
    return {
        "trade_count": len(rows),
        "maker_buy_notional": maker_buy,
        "maker_sell_notional": maker_sell,
        "taker_buy_notional": taker_buy,
        "taker_sell_notional": taker_sell,
        "taker_imbalance": 0.0 if total == 0 else (taker_buy - taker_sell) / total,
    }


def hyperliquid_trade_metrics(rows: Any) -> dict[str, float | int]:
    if not isinstance(rows, list):
        raise ForwardDataError("Hyperliquid recent trades must be a list")
    reported_buy = reported_sell = 0.0
    for row in rows:
        if not isinstance(row, dict):
            raise ForwardDataError("Malformed Hyperliquid trade")
        size = _float(row.get("sz"), "hl_trade.sz")
        if size < 0:
            raise ForwardDataError("Hyperliquid trade size is negative")
        notional = _float(row.get("px"), "hl_trade.px", positive=True) * size
        side = str(row.get("side", "")).upper()
        if side == "B":
            reported_buy += notional
        elif side == "A":
            reported_sell += notional
        else:
            raise ForwardDataError(f"Unknown Hyperliquid reported side: {side}")
    total = reported_buy + reported_sell
    return {
        "trade_count": len(rows),
        "reported_buy_notional": reported_buy,
        "reported_sell_notional": reported_sell,
        "reported_side_imbalance": 0.0 if total == 0 else (reported_buy - reported_sell) / total,
    }


def latest_csv_values(raw: bytes, columns: tuple[str, ...]) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ForwardDataError("CSV source is not UTF-8") from exc
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ForwardDataError("CSV source has no data rows")
    date_key = next((key for key in ("Date", "date", "DATE", "observation_date", "time") if key in rows[0]), "DATE")
    for row in reversed(rows):
        values: dict[str, float] = {}
        for column in columns:
            raw_value = row.get(column)
            if raw_value not in {None, "", "."}:
                values[column] = _float(raw_value, column)
        if values:
            return {"observation_date": row.get(date_key), "values": values}
    raise ForwardDataError(f"CSV source has no usable values for {columns}")


def _hyperliquid_contexts(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, list) or len(payload) != 2:
        raise ForwardDataError("Unexpected metaAndAssetCtxs response")
    meta, contexts = payload
    universe = meta.get("universe") if isinstance(meta, dict) else None
    if not isinstance(universe, list) or not isinstance(contexts, list) or len(universe) != len(contexts):
        raise ForwardDataError("Hyperliquid universe/context mismatch")
    mapped: dict[str, dict[str, Any]] = {}
    for asset, context in zip(universe, contexts, strict=True):
        if isinstance(asset, dict) and isinstance(context, dict) and asset.get("name"):
            mapped[str(asset["name"]).upper()] = context
    return mapped


def _record_source(
    raw_dir: Path,
    entries: list[SourceEntry],
    name: str,
    request_data: dict[str, Any],
    raw: bytes,
) -> None:
    digest = sha256_bytes(raw)
    path = raw_dir / f"{name}-{digest}.raw"
    if path.exists() and path.read_bytes() != raw:
        raise ForwardDataError(f"Hash collision or changed raw source at {path}")
    if not path.exists():
        _atomic_write(path, raw)
    entries.append(SourceEntry(name, request_data, f"raw/{raw_dir.name}/{path.name}", digest))


class ForwardMarketStateCollector:
    def __init__(self, client: HTTPClient | None = None, *, book_depth: int = 10):
        self.client = client or PublicHTTPClient()
        self.book_depth = book_depth

    def collect(
        self,
        out_dir: str | Path,
        *,
        assets: tuple[str, ...] = tuple(ASSET_PRODUCTS),
        captured_at: datetime | None = None,
        include_external: bool = True,
    ) -> dict[str, Any]:
        captured = _utc(captured_at)
        snapshot_id = captured.strftime("%Y%m%dT%H%M%S.%fZ")
        root = Path(out_dir)
        raw_dir = root / "raw" / snapshot_id
        normalized_path = root / "normalized" / f"{snapshot_id}.json"
        manifest_path = root / "manifests" / f"{snapshot_id}.json"
        if normalized_path.exists() or manifest_path.exists():
            raise ForwardDataError(f"Snapshot ID already exists: {snapshot_id}")

        selected = tuple(asset.strip().upper() for asset in assets)
        unknown = sorted(set(selected) - set(ASSET_PRODUCTS))
        if unknown:
            raise ForwardDataError(f"Unknown frozen assets: {', '.join(unknown)}")
        entries: list[SourceEntry] = []
        source_errors: dict[str, str] = {}
        contexts_payload = self._json_source(
            "hyperliquid-meta-contexts",
            HYPERLIQUID_INFO,
            {"type": "metaAndAssetCtxs"},
            raw_dir,
            entries,
            source_errors,
        )
        contexts: dict[str, dict[str, Any]] = {}
        if contexts_payload is not None:
            try:
                contexts = _hyperliquid_contexts(contexts_payload)
            except ForwardDataError as exc:
                source_errors["hyperliquid-meta-contexts"] = str(exc)

        asset_records: dict[str, Any] = {}
        for asset in selected:
            asset_records[asset] = self._collect_asset(
                asset,
                contexts.get(asset),
                raw_dir,
                entries,
                source_errors,
            )

        global_record = self._collect_external(raw_dir, entries, source_errors, captured) if include_external else {
            "available": False,
            "reason": "disabled_by_caller",
            "coinmetrics": {},
            "fred": {},
        }
        snapshot: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "paper_only": True,
            "authorizes_trading": False,
            "snapshot_id": snapshot_id,
            "captured_at_utc": captured.isoformat().replace("+00:00", "Z"),
            "hour_bucket_utc": captured.replace(minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z"),
            "assets": asset_records,
            "global": global_record,
            "liquidation_events": {
                "available": False,
                "reason": "no_verified_public_aggregate_adapter",
                "events": [],
            },
            "source_errors": source_errors,
        }
        snapshot_sha = sha256_bytes(canonical_json(snapshot).encode("utf-8"))
        snapshot["record_sha256"] = snapshot_sha
        normalized_bytes = (json.dumps(snapshot, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _atomic_write(normalized_path, normalized_bytes)

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "record_sha256": snapshot_sha,
            "normalized_path": f"normalized/{snapshot_id}.json",
            "normalized_file_sha256": sha256_bytes(normalized_bytes),
            "sources": [entry.__dict__ for entry in sorted(entries, key=lambda item: item.source)],
            "source_errors": source_errors,
        }
        _atomic_write(manifest_path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        return {"snapshot": snapshot, "manifest": manifest, "paths": {"normalized": str(normalized_path), "manifest": str(manifest_path)}}

    def _json_source(
        self,
        name: str,
        url: str,
        payload: dict[str, Any] | None,
        raw_dir: Path,
        entries: list[SourceEntry],
        source_errors: dict[str, str],
    ) -> Any | None:
        try:
            data, raw = self.client.json_request(url, payload=payload)
            _record_source(
                raw_dir,
                entries,
                name,
                {"url": url, "method": "POST" if payload is not None else "GET", "payload": payload},
                raw,
            )
            return data
        except Exception as exc:
            source_errors[name] = str(exc)
            return None

    def _bytes_source(
        self,
        name: str,
        url: str,
        raw_dir: Path,
        entries: list[SourceEntry],
        source_errors: dict[str, str],
    ) -> bytes | None:
        try:
            raw = self.client.bytes_request(url)
            _record_source(raw_dir, entries, name, {"url": url, "method": "GET"}, raw)
            return raw
        except Exception as exc:
            source_errors[name] = str(exc)
            return None

    def _collect_asset(
        self,
        asset: str,
        context: dict[str, Any] | None,
        raw_dir: Path,
        entries: list[SourceEntry],
        source_errors: dict[str, str],
    ) -> dict[str, Any]:
        product = ASSET_PRODUCTS[asset]
        ticker = self._json_source(
            f"coinbase-{asset}-ticker",
            f"{COINBASE_BASE}/products/{product}/ticker",
            None,
            raw_dir,
            entries,
            source_errors,
        )
        spot_book = self._json_source(
            f"coinbase-{asset}-book",
            f"{COINBASE_BASE}/products/{product}/book?level=2",
            None,
            raw_dir,
            entries,
            source_errors,
        )
        spot_trades = self._json_source(
            f"coinbase-{asset}-trades",
            f"{COINBASE_BASE}/products/{product}/trades?limit=1000",
            None,
            raw_dir,
            entries,
            source_errors,
        )
        perp_book = self._json_source(
            f"hyperliquid-{asset}-book",
            HYPERLIQUID_INFO,
            {"type": "l2Book", "coin": asset},
            raw_dir,
            entries,
            source_errors,
        )
        perp_trades = self._json_source(
            f"hyperliquid-{asset}-trades",
            HYPERLIQUID_INFO,
            {"type": "recentTrades", "coin": asset},
            raw_dir,
            entries,
            source_errors,
        )
        record: dict[str, Any] = {
            "coinbase_product": product,
            "hyperliquid_coin": asset,
            "spot_quote": {"available": False},
            "spot_book": {"available": False},
            "spot_trade_flow": {"available": False},
            "perp_state": {"available": False},
            "perp_book": {"available": False},
            "perp_trade_flow": {"available": False},
            "cross_venue": {"available": False},
        }
        if isinstance(ticker, dict):
            try:
                bid = _float(ticker.get("bid"), "ticker.bid", positive=True)
                ask = _float(ticker.get("ask"), "ticker.ask", positive=True)
                if bid >= ask:
                    raise ForwardDataError("Coinbase ticker is crossed")
                mid = (bid + ask) / 2.0
                record["spot_quote"] = {
                    "available": True,
                    "last": _float(ticker.get("price"), "ticker.price", positive=True),
                    "bid": bid,
                    "ask": ask,
                    "mid": mid,
                    "spread_bps": (ask - bid) / mid * 10_000.0,
                    "volume_24h_base": _float(ticker.get("volume"), "ticker.volume"),
                    "source_time": ticker.get("time"),
                }
            except ForwardDataError as exc:
                source_errors[f"normalized-{asset}-spot-quote"] = str(exc)

        if isinstance(spot_book, dict):
            try:
                metrics = order_book_metrics(spot_book.get("bids"), spot_book.get("asks"), self.book_depth)
                record["spot_book"] = {"available": True, **metrics.__dict__, "sequence": spot_book.get("sequence")}
            except ForwardDataError as exc:
                source_errors[f"normalized-{asset}-spot-book"] = str(exc)

        if spot_trades is not None:
            try:
                record["spot_trade_flow"] = {"available": True, **coinbase_trade_metrics(spot_trades)}
            except ForwardDataError as exc:
                source_errors[f"normalized-{asset}-spot-trades"] = str(exc)
        if isinstance(context, dict):
            try:
                mark = _float(context.get("markPx"), "context.markPx", positive=True)
                oracle = _float(context.get("oraclePx"), "context.oraclePx", positive=True)
                funding = _float(context.get("funding"), "context.funding")
                open_interest = _float(context.get("openInterest"), "context.openInterest")
                day_notional = _float(context.get("dayNtlVlm"), "context.dayNtlVlm")
                if open_interest < 0 or day_notional < 0:
                    raise ForwardDataError("Hyperliquid context contains negative size/volume")
                record["perp_state"] = {
                    "available": True,
                    "mark": mark,
                    "oracle": oracle,
                    "funding": funding,
                    "open_interest_base": open_interest,
                    "open_interest_usd": open_interest * mark,
                    "day_notional_volume_usd": day_notional,
                    "mark_oracle_basis_bps": (mark / oracle - 1.0) * 10_000.0,
                    "premium": None if context.get("premium") in {None, ""} else _float(context.get("premium"), "context.premium"),
                }
            except ForwardDataError as exc:
                source_errors[f"normalized-{asset}-perp-state"] = str(exc)
        else:
            source_errors[f"normalized-{asset}-perp-state"] = "asset missing from metaAndAssetCtxs"
        if isinstance(perp_book, dict):
            try:
                levels = perp_book.get("levels")
                if not isinstance(levels, list) or len(levels) != 2:
                    raise ForwardDataError("Hyperliquid book levels are malformed")
                metrics = order_book_metrics(levels[0], levels[1], self.book_depth)
                record["perp_book"] = {"available": True, **metrics.__dict__, "source_time_ms": perp_book.get("time")}
            except ForwardDataError as exc:
                source_errors[f"normalized-{asset}-perp-book"] = str(exc)

        if perp_trades is not None:
            try:
                record["perp_trade_flow"] = {"available": True, **hyperliquid_trade_metrics(perp_trades)}
            except ForwardDataError as exc:
                source_errors[f"normalized-{asset}-perp-trades"] = str(exc)

        if record["spot_quote"]["available"] and record["perp_state"]["available"]:
            spot_mid = float(record["spot_quote"]["mid"])
            mark = float(record["perp_state"]["mark"])
            record["cross_venue"] = {
                "available": True,
                "spot_perp_basis": mark / spot_mid - 1.0,
                "spot_perp_basis_bps": (mark / spot_mid - 1.0) * 10_000.0,
                "absolute_dispersion_bps": abs(mark / spot_mid - 1.0) * 10_000.0,
            }
        return record

    def _collect_external(
        self,
        raw_dir: Path,
        entries: list[SourceEntry],
        source_errors: dict[str, str],
        captured: datetime,
    ) -> dict[str, Any]:
        coinmetrics: dict[str, Any] = {}
        for asset in COINMETRICS_ASSETS:
            name = f"coinmetrics-{asset}"
            raw = self._bytes_source(name, f"{COINMETRICS_BASE}/{asset}.csv", raw_dir, entries, source_errors)
            if raw is None:
                coinmetrics[asset.upper()] = {"available": False, "reason": source_errors.get(name)}
                continue
            try:
                values = latest_csv_values(raw, COINMETRICS_METRICS)
                coinmetrics[asset.upper()] = {
                    "available": True,
                    **values,
                    "staleness_days": _staleness_days(values.get("observation_date"), captured),
                }
            except ForwardDataError as exc:
                source_errors[f"normalized-{name}"] = str(exc)
                coinmetrics[asset.upper()] = {"available": False, "reason": str(exc)}

        fred: dict[str, Any] = {}
        for series in FRED_SERIES:
            name = f"fred-{series}"
            raw = self._bytes_source(name, FRED_CSV.format(series=series), raw_dir, entries, source_errors)
            if raw is None:
                fred[series] = {"available": False, "reason": source_errors.get(name)}
                continue
            try:
                values = latest_csv_values(raw, (series,))
                fred[series] = {
                    "available": True,
                    **values,
                    "staleness_days": _staleness_days(values.get("observation_date"), captured),
                }
            except ForwardDataError as exc:
                source_errors[f"normalized-{name}"] = str(exc)
                fred[series] = {"available": False, "reason": str(exc)}

        available = all(item.get("available") for item in coinmetrics.values()) and all(
            item.get("available") for item in fred.values()
        )
        return {
            "available": available,
            "reason": None if available else "one_or_more_daily_sources_unavailable",
            "coinmetrics": coinmetrics,
            "fred": fred,
        }


def _staleness_days(observation_date: Any, captured: datetime) -> int | None:
    if not observation_date:
        return None
    try:
        observed = datetime.fromisoformat(str(observation_date)).date()
    except ValueError:
        return None
    return (captured.date() - observed).days


def _parse_captured_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--captured-at must be ISO-8601") from exc
    return _utc(parsed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect a read-only, provenance-hashed crypto market-state snapshot.")
    parser.add_argument("--out", default="data/forward-market-state")
    parser.add_argument("--assets", default=",".join(ASSET_PRODUCTS))
    parser.add_argument("--captured-at")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--book-depth", type=int, default=10)
    parser.add_argument("--external", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)
    assets = tuple(item.strip().upper() for item in args.assets.split(",") if item.strip())
    collector = ForwardMarketStateCollector(
        PublicHTTPClient(timeout=args.timeout, retries=args.retries),
        book_depth=args.book_depth,
    )
    result = collector.collect(
        args.out,
        assets=assets,
        captured_at=_parse_captured_at(args.captured_at),
        include_external=args.external,
    )
    print(json.dumps({
        "snapshot_id": result["snapshot"]["snapshot_id"],
        "record_sha256": result["snapshot"]["record_sha256"],
        "normalized": result["paths"]["normalized"],
        "manifest": result["paths"]["manifest"],
        "source_errors": result["snapshot"]["source_errors"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
