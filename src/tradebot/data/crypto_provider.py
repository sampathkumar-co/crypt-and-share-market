from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from tradebot.data.csv_loader import load_candles
from tradebot.models import Candle


class CryptoDataError(RuntimeError):
    """Raised when a public read-only crypto market-data fetch fails."""


@dataclass(frozen=True)
class FetchResult:
    symbol: str
    path: Path | None
    candles: int
    error: str | None = None


class CryptoCSVProvider:
    """Paper-only CSV provider; never connects to exchanges, wallets, or order APIs."""

    def get_history(self, path: str | Path) -> list[Candle]:
        return load_candles(path)


class PublicCryptoHistoricalClient:
    """Read-only public OHLCV client using Binance with CoinGecko fallback."""

    base_url = "https://api.binance.com"
    max_limit = 1000

    def __init__(self, timeout: float = 20.0, retries: int = 3, backoff_seconds: float = 1.0, use_fallback: bool = True):
        self.timeout = timeout
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self.use_fallback = use_fallback
        self.fallback = CoinGeckoHistoricalClient(timeout=timeout, retries=retries, backoff_seconds=backoff_seconds) if use_fallback else None

    def fetch_symbol(self, symbol: str, interval: str = "1d", days: int = 365) -> list[Candle]:
        symbol = symbol.strip().upper()
        if not symbol:
            raise CryptoDataError("Symbol cannot be empty")
        if days <= 0:
            raise CryptoDataError("Days must be positive")

        try:
            rows: list[Any] = self._request_klines(symbol, interval, min(days, self.max_limit))
            candles = normalize_binance_klines(rows)
        except Exception as exc:
            if not self.fallback:
                raise
            print(f"WARNING: Binance public data failed for {symbol}; trying CoinGecko fallback: {exc}")
            candles = self.fallback.fetch_symbol(symbol, interval=interval, days=days)
        min_candles = min(days, 5)
        validate_fetched_candles(candles, min_candles=min_candles)
        return candles[-days:]

    def fetch_symbols(self, symbols: list[str], interval: str = "1d", days: int = 365) -> dict[str, list[Candle]]:
        fetched: dict[str, list[Candle]] = {}
        failures: dict[str, str] = {}
        for symbol in symbols:
            clean_symbol = symbol.strip().upper()
            try:
                fetched[clean_symbol] = self.fetch_symbol(clean_symbol, interval=interval, days=days)
            except Exception as exc:  # keep fetching remaining symbols and report clear failures
                failures[clean_symbol] = str(exc)
        if failures:
            failure_text = "; ".join(f"{symbol}: {error}" for symbol, error in failures.items())
            print(f"WARNING: Some public crypto data fetches failed: {failure_text}")
        return fetched

    def fetch_symbols_to_csv(
        self,
        symbols: list[str],
        interval: str = "1d",
        days: int = 365,
        out_dir: str | Path = "data/crypto",
    ) -> list[FetchResult]:
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        results: list[FetchResult] = []
        for symbol in symbols:
            clean_symbol = symbol.strip().upper()
            try:
                candles = self.fetch_symbol(clean_symbol, interval=interval, days=days)
                csv_path = save_candles_csv(clean_symbol, candles, out_path)
                results.append(FetchResult(clean_symbol, csv_path, len(candles)))
            except Exception as exc:  # continue fetching other symbols on rate limits or bad symbols
                results.append(FetchResult(clean_symbol, None, 0, str(exc)))
        return results

    def _request_klines(self, symbol: str, interval: str, limit: int) -> list[Any]:
        query = urlencode({"symbol": symbol, "interval": interval, "limit": limit})
        url = f"{self.base_url}/api/v3/klines?{query}"
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                with urlopen(url, timeout=self.timeout) as response:  # noqa: S310 - public market data URL
                    if response.status != 200:
                        raise CryptoDataError(f"Binance returned HTTP {response.status} for {symbol}")
                    payload = json.loads(response.read().decode("utf-8"))
                    if not isinstance(payload, list):
                        raise CryptoDataError(f"Unexpected Binance response for {symbol}: {payload}")
                    return payload
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {418, 429, 500, 502, 503, 504}:
                    break
            except (URLError, TimeoutError, json.JSONDecodeError, CryptoDataError) as exc:
                last_error = exc
            if attempt < self.retries:
                time.sleep(self.backoff_seconds * attempt)
        raise CryptoDataError(f"Failed to fetch {symbol} {interval} candles after {self.retries} attempts: {last_error}")


class CoinGeckoHistoricalClient:
    """Read-only fallback for daily spot crypto OHLCV using public CoinGecko data."""

    base_url = "https://api.coingecko.com/api/v3"
    symbol_to_id = {
        "BTCUSDT": "bitcoin",
        "ETHUSDT": "ethereum",
        "SOLUSDT": "solana",
        "BNBUSDT": "binancecoin",
        "XRPUSDT": "ripple",
        "ADAUSDT": "cardano",
        "DOGEUSDT": "dogecoin",
    }

    def __init__(self, timeout: float = 20.0, retries: int = 3, backoff_seconds: float = 1.0):
        self.timeout = timeout
        self.retries = retries
        self.backoff_seconds = backoff_seconds

    def fetch_symbol(self, symbol: str, interval: str = "1d", days: int = 365) -> list[Candle]:
        if interval != "1d":
            raise CryptoDataError("CoinGecko fallback currently supports interval=1d only")
        coin_id = self.symbol_to_id.get(symbol.upper())
        if not coin_id:
            raise CryptoDataError(f"CoinGecko fallback does not know symbol mapping for {symbol}")
        ohlc = self._request_json(f"{self.base_url}/coins/{coin_id}/ohlc?{urlencode({'vs_currency': 'usd', 'days': days})}")
        market = self._request_json(f"{self.base_url}/coins/{coin_id}/market_chart?{urlencode({'vs_currency': 'usd', 'days': days, 'interval': 'daily'})}")
        volumes = _volumes_by_day(market.get("total_volumes", []) if isinstance(market, dict) else [])
        candles: list[Candle] = []
        for row in ohlc:
            if not isinstance(row, list) or len(row) < 5:
                raise CryptoDataError("CoinGecko OHLC row is missing required fields")
            timestamp = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).replace(tzinfo=None)
            candles.append(
                Candle(
                    timestamp=timestamp,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(volumes.get(timestamp.date().isoformat(), 0.0)),
                )
            )
        return sorted(candles, key=lambda candle: candle.timestamp)

    def _request_json(self, url: str) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                with urlopen(url, timeout=self.timeout) as response:  # noqa: S310 - public market data URL
                    if response.status != 200:
                        raise CryptoDataError(f"CoinGecko returned HTTP {response.status}")
                    return json.loads(response.read().decode("utf-8"))
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, CryptoDataError) as exc:
                last_error = exc
            if attempt < self.retries:
                time.sleep(self.backoff_seconds * attempt)
        raise CryptoDataError(f"CoinGecko fallback failed after {self.retries} attempts: {last_error}")


def _volumes_by_day(rows: list[Any]) -> dict[str, float]:
    volumes: dict[str, float] = {}
    for row in rows:
        if isinstance(row, list) and len(row) >= 2:
            day = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).date().isoformat()
            volumes[day] = float(row[1])
    return volumes


def normalize_binance_klines(rows: list[Any]) -> list[Candle]:
    candles_by_timestamp: dict[datetime, Candle] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            raise CryptoDataError("Binance kline row is missing required OHLCV fields")
        timestamp = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).replace(tzinfo=None)
        candle = Candle(
            timestamp=timestamp,
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
        candles_by_timestamp[timestamp] = candle
    return sorted(candles_by_timestamp.values(), key=lambda candle: candle.timestamp)


def validate_fetched_candles(candles: list[Candle], min_candles: int = 5) -> None:
    if not candles:
        raise CryptoDataError("Public crypto data source returned empty candle data")
    if len(candles) < min_candles:
        raise CryptoDataError(f"Too few candles fetched: expected at least {min_candles}, got {len(candles)}")
    seen: set[datetime] = set()
    for candle in candles:
        if candle.timestamp in seen:
            raise CryptoDataError(f"Duplicate timestamp after cleanup: {candle.timestamp.isoformat()}")
        seen.add(candle.timestamp)
        if min(candle.open, candle.high, candle.low, candle.close) <= 0 or candle.volume < 0:
            raise CryptoDataError(f"Invalid OHLCV values for {candle.timestamp.isoformat()}")
        if candle.high < max(candle.open, candle.close) or candle.low > min(candle.open, candle.close):
            raise CryptoDataError(f"Inconsistent high/low for {candle.timestamp.isoformat()}")


def save_candles_csv(symbol: str, candles: list[Candle], out_dir: str | Path) -> Path:
    validate_fetched_candles(candles)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    csv_path = out_path / f"{symbol.strip().upper()}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for candle in sorted(candles, key=lambda item: item.timestamp):
            writer.writerow([
                candle.timestamp.isoformat(),
                f"{candle.open:.10g}",
                f"{candle.high:.10g}",
                f"{candle.low:.10g}",
                f"{candle.close:.10g}",
                f"{candle.volume:.10g}",
            ])
    load_candles(csv_path)
    return csv_path
