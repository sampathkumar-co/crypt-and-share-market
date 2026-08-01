from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

from . import learned_daily_multihorizon_v41 as learned

SCHEMA_VERSION = "4.1-daily-shadow-smoke"
DEFAULT_BALANCE = 10_000.0


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_price(asset: str, timeout: float = 10.0) -> float:
    url = f"https://api.exchange.coinbase.com/products/{learned.PRODUCTS[asset]}/ticker"
    request = Request(url, headers={"User-Agent": "tradebot-v41-shadow/1.0"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    price = float(payload["price"])
    if price <= 0.0:
        raise RuntimeError(f"invalid {asset} price")
    return price


def fetch_prices() -> dict[str, float]:
    return {asset: fetch_price(asset) for asset in learned.ASSETS}


def run_smoke(
    historical: dict[str, Any],
    *,
    duration_seconds: int = 600,
    poll_seconds: int = 30,
    price_fetcher: Callable[[], dict[str, float]] = fetch_prices,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    target = historical["current_reasoning"]["target_weights"]
    cash = DEFAULT_BALANCE
    quantities = {asset: 0.0 for asset in learned.ASSETS}
    costs = 0.0
    prices = price_fetcher()
    for asset, weight in target.items():
        notional = DEFAULT_BALANCE * float(weight)
        cost = notional * learned.STANDARD_ONE_WAY_COST
        cash -= notional + cost
        quantities[asset] = notional / prices[asset]
        costs += cost
    start = now_fn()
    deadline = start.timestamp() + duration_seconds
    samples: list[dict[str, Any]] = []
    errors: list[str] = []
    peak = DEFAULT_BALANCE
    maximum_drawdown = 0.0

    while now_fn().timestamp() < deadline:
        try:
            prices = price_fetcher()
            equity = cash + sum(quantities[asset] * prices[asset] for asset in learned.ASSETS)
            peak = max(peak, equity)
            maximum_drawdown = max(maximum_drawdown, 1.0 - equity / peak)
            samples.append({
                "timestamp_utc": utc_iso(now_fn()),
                "prices": prices,
                "equity": equity,
                "cash": cash,
                "positions": {asset: qty for asset, qty in quantities.items() if qty > 0.0},
            })
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        sleep_fn(float(poll_seconds))

    try:
        prices = price_fetcher()
        for asset, quantity in list(quantities.items()):
            if quantity <= 0.0:
                continue
            proceeds = quantity * prices[asset]
            cost = proceeds * learned.STANDARD_ONE_WAY_COST
            cash += proceeds - cost
            costs += cost
            quantities[asset] = 0.0
    except Exception as exc:
        errors.append(f"liquidation {type(exc).__name__}: {exc}")
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": "CURRENT_MARKET_TEN_MINUTE_DAILY_SHADOW_SMOKE_ONLY",
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": True,
        "started_at_utc": utc_iso(start),
        "ended_at_utc": utc_iso(now_fn()),
        "requested_duration_seconds": duration_seconds,
        "poll_seconds": poll_seconds,
        "sample_count": len(samples),
        "initial_equity": DEFAULT_BALANCE,
        "final_equity": cash,
        "net_return": cash / DEFAULT_BALANCE - 1.0,
        "maximum_drawdown": maximum_drawdown,
        "costs_paid": costs,
        "target_weights": target,
        "final_positions": {asset: qty for asset, qty in quantities.items() if qty > 0.0},
        "historical_report_sha256": historical["report_sha256"],
        "historical_status": historical["evaluation"]["status"],
        "samples": samples,
        "errors": errors,
        "smoke_passed": bool(samples) and not errors and not any(quantities.values()),
        "profitability_proven": False,
    }
    report["report_sha256"] = hashlib.sha256(
        learned.canonical_json(report).encode()
    ).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run v4.1 daily shadow smoke")
    parser.add_argument("--historical-json", required=True)
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--duration-seconds", type=int, default=600)
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args(argv)
    historical = json.loads(Path(args.historical_json).read_text(encoding="utf-8"))
    report = run_smoke(
        historical,
        duration_seconds=args.duration_seconds,
        poll_seconds=args.poll_seconds,
    )
    output = Path(args.json_out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "smoke_passed": report["smoke_passed"],
        "samples": report["sample_count"],
        "net_return": report["net_return"],
        "errors": report["errors"],
        "report_sha256": report["report_sha256"],
    }, indent=2, sort_keys=True))
    return 0 if report["smoke_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
