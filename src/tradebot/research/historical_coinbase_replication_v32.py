from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import historical_proxy_screen_v25 as v25
from tradebot.research import historical_yield_trend_v31 as v31
from tradebot.research import historical_yield_trend_v311_transport as cash_transport
from tradebot.research import historical_yield_trend_scheduled_execution_v312 as execution

MODE = "HISTORICAL_COINBASE_EXACT_MODEL_REPLICATION_ONLY"
SCHEMA_VERSION = "3.2-coinbase-exact-replication"
PROTOCOL_PATH = Path("research/V32_COINBASE_EXACT_MODEL_REPLICATION_PROTOCOL.md")
DEPENDENCY_PATH = Path("research/V32_EXECUTION_DEPENDENCY_ADDENDUM.md")
RESULT_STATUS_PASS = "VERIFIED_FIVE_YEAR_COINBASE_REPLICATION"
RESULT_STATUS_FAIL = "NOT_VERIFIED_FIVE_YEAR_COINBASE_REPLICATION"
EXPECTED_V312_REPORT_SHA256 = (
    "90dea7bcc12274146f730ba5a5cd9f93179ff944211ff07de849aca68e468c22"
)

ASSETS = v31.ASSETS
PRODUCTS = {"BTC": "BTC-USD", "ETH": "ETH-USD"}
BASE_URL = "https://api.exchange.coinbase.com"
DATA_START = datetime(2020, 6, 14, tzinfo=timezone.utc)
EXIT_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
CHUNK_DAYS = 250
GRANULARITY = 86_400

FROZEN_MODEL = v31.ModelSpec(
    sma_length=100,
    rebalance_days=10,
    top_n=1,
    maximum_exposure=0.10,
    volatility_target=0.02,
    drawdown_brake=0.20,
)
EXPECTED_MODEL_ID = "sma100-rebalance10-top1-exposure10-vol2-brake20"

# Test-compatible aliases to the exact integrity-gate implementation.
_summarize = execution.summarize_years
_evaluate_gates = execution.evaluate_integrity_gates


class CoinbaseReplicationV32Error(RuntimeError):
    """Raised when the v3.2 replication cannot be reproduced safely."""


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _days(start: datetime, end: datetime) -> list[datetime]:
    values: list[datetime] = []
    day = start
    while day <= end:
        values.append(day)
        day += timedelta(days=1)
    return values


def _request_ranges(
    start: datetime = DATA_START,
    end: datetime = EXIT_DATE,
) -> list[tuple[datetime, datetime]]:
    if start > end:
        raise CoinbaseReplicationV32Error("Coinbase request start exceeds end")
    ranges: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor <= end:
        finish = min(end, cursor + timedelta(days=CHUNK_DAYS - 1))
        ranges.append((cursor, finish))
        cursor = finish + timedelta(days=1)
    return ranges


def _candle_url(product: str, start: datetime, end: datetime) -> str:
    query = urlencode(
        {
            "granularity": str(GRANULARITY),
            "start": _utc(start),
            "end": _utc(end + timedelta(days=1)),
        }
    )
    return f"{BASE_URL}/products/{product}/candles?{query}"


def _download_json(
    url: str,
    *,
    timeout: float = 30.0,
    attempts: int = 3,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[bytes, str]:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        request = Request(
            url,
            headers={
                "User-Agent": "tradebot-v32-coinbase-replication/1.0",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                if response.status != 200:
                    raise CoinbaseReplicationV32Error(
                        f"Coinbase returned HTTP {response.status}"
                    )
                content = response.read()
            if not content:
                raise CoinbaseReplicationV32Error(
                    "Coinbase returned an empty response"
                )
            return content, hashlib.sha256(content).hexdigest()
        except (
            HTTPError,
            URLError,
            TimeoutError,
            CoinbaseReplicationV32Error,
        ) as exc:
            last_error = exc
            if attempt < attempts:
                sleeper(float(attempt))
    raise CoinbaseReplicationV32Error(
        f"Coinbase download failed after {attempts} attempts: {last_error}"
    )


def _parse_coinbase_candles(
    content: bytes,
    *,
    asset: str,
    requested_start: datetime,
    requested_end: datetime,
) -> dict[datetime, v25.HourlyBar]:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoinbaseReplicationV32Error(
            f"Coinbase {asset} response is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(payload, list):
        raise CoinbaseReplicationV32Error(
            f"Coinbase {asset} response is not a candle list"
        )
    bars: dict[datetime, v25.HourlyBar] = {}
    for row in payload:
        if not isinstance(row, list) or len(row) < 6:
            raise CoinbaseReplicationV32Error(
                f"Coinbase {asset} candle has invalid schema: {row!r}"
            )
        try:
            timestamp = datetime.fromtimestamp(int(row[0]), tz=timezone.utc)
            timestamp = timestamp.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            low = float(row[1])
            high = float(row[2])
            open_price = float(row[3])
            close = float(row[4])
            volume = float(row[5])
        except (TypeError, ValueError, OverflowError) as exc:
            raise CoinbaseReplicationV32Error(
                f"Coinbase {asset} candle contains invalid values: {row!r}"
            ) from exc
        if not requested_start <= timestamp <= requested_end:
            continue
        if min(low, high, open_price, close) <= 0.0 or volume < 0.0:
            raise CoinbaseReplicationV32Error(
                f"Coinbase {asset} candle contains invalid price or volume"
            )
        if (
            low > min(open_price, close)
            or high < max(open_price, close)
            or high < low
        ):
            raise CoinbaseReplicationV32Error(
                f"Coinbase {asset} candle violates OHLC ordering"
            )
        bar = v25.HourlyBar(
            hour=timestamp,
            open=open_price,
            high=high,
            low=low,
            close=close,
            quote_volume=volume * close,
            taker_buy_quote_volume=0.0,
        )
        prior = bars.get(timestamp)
        if prior is not None and prior != bar:
            raise CoinbaseReplicationV32Error(
                f"Coinbase {asset} conflicting duplicate {timestamp.date()}"
            )
        bars[timestamp] = bar
    return bars


def download_coinbase_bars(
    *,
    downloader: Callable[..., tuple[bytes, str]] = _download_json,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[
    dict[str, dict[datetime, v25.HourlyBar]],
    list[dict[str, Any]],
]:
    bars: dict[str, dict[datetime, v25.HourlyBar]] = {
        asset: {} for asset in ASSETS
    }
    inventory: list[dict[str, Any]] = []
    for asset in ASSETS:
        product = PRODUCTS[asset]
        for index, (start, end) in enumerate(_request_ranges()):
            url = _candle_url(product, start, end)
            content, digest = downloader(url)
            parsed = _parse_coinbase_candles(
                content,
                asset=asset,
                requested_start=start,
                requested_end=end,
            )
            for day, bar in parsed.items():
                prior = bars[asset].get(day)
                if prior is not None and prior != bar:
                    raise CoinbaseReplicationV32Error(
                        f"Coinbase {asset} cross-chunk conflict {day.date()}"
                    )
                bars[asset][day] = bar
            inventory.append(
                {
                    "key": f"coinbase:{asset}:{index:02d}",
                    "provider": "coinbase-exchange-public-rest",
                    "product": product,
                    "start": start.date().isoformat(),
                    "end": end.date().isoformat(),
                    "url": url,
                    "sha256": digest,
                    "rows": len(parsed),
                }
            )
            sleeper(0.15)

    required = _days(DATA_START, EXIT_DATE)
    for asset in ASSETS:
        missing = [day for day in required if day not in bars[asset]]
        if missing:
            raise CoinbaseReplicationV32Error(
                f"Coinbase {asset} missing {len(missing)} required candles; "
                f"first={missing[0].date()}"
            )
    return bars, inventory


def run_replication(
    *,
    coinbase_downloader: Callable[..., tuple[bytes, str]] = _download_json,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    for path in (PROTOCOL_PATH, DEPENDENCY_PATH):
        if not path.is_file():
            raise CoinbaseReplicationV32Error(f"missing frozen file: {path}")
    if FROZEN_MODEL.model_id != EXPECTED_MODEL_ID:
        raise CoinbaseReplicationV32Error("frozen model identity changed")

    bars, inventory = download_coinbase_bars(
        downloader=coinbase_downloader,
        sleeper=sleeper,
    )
    normalized_cash, cash_inventory = (
        cash_transport.download_cash_series_with_resilience()
    )
    inventory.append(
        {
            **cash_inventory,
            "provider": cash_transport.TRANSPORT_AUDIT["selected_source"],
            "series_id": cash_transport.FED_H15_SERIES,
            "normalized_sha256": cash_transport.TRANSPORT_AUDIT[
                "normalized_sha256"
            ],
            "rows": cash_transport.TRANSPORT_AUDIT["observation_count"],
        }
    )
    inventory.sort(key=lambda item: item["key"])

    dates = _days(DATA_START, EXIT_DATE)
    features = v31.build_features(bars, dates)
    rates = cash_transport.parse_fred_rates(normalized_cash)
    cash_returns = v31.build_daily_cash_returns(rates, dates)

    standard_results: dict[str, v31.SimulationResult] = {}
    stress_results: dict[str, v31.SimulationResult] = {}
    for period in v31.VERIFICATION_PERIODS:
        standard_results[period.name] = execution.simulate_scheduled(
            FROZEN_MODEL,
            bars,
            features,
            cash_returns,
            period.start,
            period.end,
            v31.STANDARD_COST,
        )
        stress_results[period.name] = execution.simulate_scheduled(
            FROZEN_MODEL,
            bars,
            features,
            cash_returns,
            period.start,
            period.end,
            v31.STRESS_COST,
        )

    standard = execution.summarize_years(standard_results)
    stress = execution.summarize_years(stress_results)
    gates = execution.evaluate_integrity_gates(standard, stress)
    accepted = all(gates.values())
    source_fingerprint = hashlib.sha256(
        canonical_json(inventory).encode("utf-8")
    ).hexdigest()

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "changes_track_a": False,
        "cannot_replace_forward_evidence": True,
        "price_provider": "coinbase-exchange-public-rest",
        "cash_series": "DGS3MO",
        "cash_source_policy": cash_transport.CASH_SOURCE_POLICY,
        "cash_transport_policy": cash_transport.CASH_TRANSPORT_POLICY,
        "cash_transport_audit": dict(cash_transport.TRANSPORT_AUDIT),
        "execution_policy": (
            "daily_risk_exit_entry_or_due_rebalance_only_natural_drift"
        ),
        "v312_dependency_report_sha256": EXPECTED_V312_REPORT_SHA256,
        "assets": list(ASSETS),
        "products": PRODUCTS,
        "frozen_model": asdict(FROZEN_MODEL) | {
            "model_id": FROZEN_MODEL.model_id
        },
        "verification_periods": [
            {
                "name": period.name,
                "start": _utc(period.start),
                "end": _utc(period.end),
            }
            for period in v31.VERIFICATION_PERIODS
        ],
        "source_inventory": inventory,
        "source_inventory_sha256": source_fingerprint,
        "standard": standard,
        "stress": stress,
        "gates": gates,
        "screening_status": (
            RESULT_STATUS_PASS if accepted else RESULT_STATUS_FAIL
        ),
        "fingerprints": {
            "protocol_sha256": hashlib.sha256(
                PROTOCOL_PATH.read_bytes()
            ).hexdigest(),
            "dependency_addendum_sha256": hashlib.sha256(
                DEPENDENCY_PATH.read_bytes()
            ).hexdigest(),
            "implementation_sha256": hashlib.sha256(
                Path(__file__).resolve().read_bytes()
            ).hexdigest(),
            "scheduled_execution_sha256": hashlib.sha256(
                Path(execution.__file__).resolve().read_bytes()
            ).hexdigest(),
            "v31_engine_sha256": hashlib.sha256(
                Path(v31.__file__).resolve().read_bytes()
            ).hexdigest(),
            "cash_transport_sha256": hashlib.sha256(
                Path(cash_transport.__file__).resolve().read_bytes()
            ).hexdigest(),
            "frozen_model_sha256": hashlib.sha256(
                canonical_json(asdict(FROZEN_MODEL)).encode("utf-8")
            ).hexdigest(),
        },
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run corrected v3.2 Coinbase exact-model replication."
    )
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args(argv)
    report = run_replication()
    _atomic_json(Path(args.json_out), report)
    print(
        json.dumps(
            {
                "status": report["screening_status"],
                "standard_return": report["standard"][
                    "net_compounded_return"
                ],
                "stress_return": report["stress"]["net_compounded_return"],
                "cash_return": report["standard"][
                    "cash_benchmark_compounded_return"
                ],
                "standard_years": report["standard"]["window_returns"],
                "stress_years": report["stress"]["window_returns"],
                "standard_excess_years": report["standard"][
                    "excess_window_returns"
                ],
                "stress_excess_years": report["stress"][
                    "excess_window_returns"
                ],
                "action_days": report["standard"]["window_action_days"],
                "gates": report["gates"],
                "report_sha256": report["report_sha256"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
