from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tradebot.research import cost_aware_paper_execution_v56 as engine

PRODUCTS = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
    "ADA": "ADA-USD",
}
BASE_URL = "https://api.exchange.coinbase.com"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def request_json(path: str, params: dict[str, str] | None = None) -> Any:
    url = f"{BASE_URL}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"
    error: Exception | None = None
    for attempt in range(1, 5):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "tradebot-v56-paper/1.0",
                    "Accept": "application/json",
                },
            )
            with urlopen(request, timeout=20) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            error = exc
            if attempt < 4:
                time.sleep(float(attempt))
    raise RuntimeError(f"request failed for {url}: {error}")


def ticker(product: str) -> dict[str, float]:
    payload = request_json(f"/products/{product}/ticker")
    bid = float(payload["bid"])
    ask = float(payload["ask"])
    price = float(payload["price"])
    if min(bid, ask, price) <= 0.0 or ask < bid:
        raise RuntimeError(f"invalid ticker for {product}")
    return {"bid": bid, "ask": ask, "price": price}


def candles(product: str, now: datetime) -> list[dict[str, float]]:
    payload = request_json(
        f"/products/{product}/candles",
        {
            "granularity": "60",
            "start": iso(now - timedelta(minutes=140)),
            "end": iso(now),
        },
    )
    rows: dict[int, dict[str, float]] = {}
    for row in payload:
        if not isinstance(row, list) or len(row) < 6:
            raise RuntimeError(f"invalid candle row for {product}")
        stamp = int(row[0])
        low, high, opened, close, volume = map(float, row[1:6])
        if min(low, high, opened, close) <= 0.0 or volume < 0.0:
            raise RuntimeError(f"invalid candle values for {product}")
        rows[stamp] = {
            "open": opened,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    ordered = [rows[key] for key in sorted(rows)]
    if len(ordered) < 65:
        raise RuntimeError(f"only {len(ordered)} candles for {product}")
    return ordered


def safe_zscores(values: dict[str, float]) -> dict[str, float]:
    numbers = list(values.values())
    mean = statistics.fmean(numbers)
    stdev = statistics.pstdev(numbers)
    if stdev <= 1e-12:
        return {key: 0.0 for key in values}
    return {key: (value - mean) / stdev for key, value in values.items()}


def raw_features(asset: str, product: str, now: datetime) -> dict[str, float | str]:
    rows = candles(product, now)
    closes = [row["close"] for row in rows]
    volumes = [row["volume"] for row in rows]
    returns = [
        math.log(closes[index] / closes[index - 1])
        for index in range(1, len(closes))
    ]
    volume_sum = sum(volumes[-20:])
    vwap20 = sum(
        close * volume
        for close, volume in zip(closes[-20:], volumes[-20:], strict=True)
    ) / max(volume_sum, 1e-12)
    quote = ticker(product)
    return {
        "asset": asset,
        "ret5": closes[-1] / closes[-6] - 1.0,
        "ret15": closes[-1] / closes[-16] - 1.0,
        "ret60": closes[-1] / closes[-61] - 1.0,
        "last_close": closes[-1],
        "vwap20": vwap20,
        "volatility60": statistics.pstdev(returns[-60:]),
        "spread_bps": (quote["ask"] / quote["bid"] - 1.0) * engine.BPS,
    }


def build_signals(now: datetime) -> list[engine.MarketSignal]:
    features = {
        asset: raw_features(asset, product, now)
        for asset, product in PRODUCTS.items()
    }
    z5 = safe_zscores({asset: float(row["ret5"]) for asset, row in features.items()})
    z15 = safe_zscores({asset: float(row["ret15"]) for asset, row in features.items()})
    z60 = safe_zscores({asset: float(row["ret60"]) for asset, row in features.items()})
    zvol = safe_zscores({
        asset: float(row["volatility60"])
        for asset, row in features.items()
    })
    signals: list[engine.MarketSignal] = []
    for asset, row in features.items():
        score = (
            0.15 * z5[asset]
            + 0.50 * z15[asset]
            + 0.35 * z60[asset]
            - 0.10 * zvol[asset]
        )
        signals.append(engine.MarketSignal(
            asset=asset,
            score=score,
            ret5=float(row["ret5"]),
            ret15=float(row["ret15"]),
            ret60=float(row["ret60"]),
            last_close=float(row["last_close"]),
            vwap20=float(row["vwap20"]),
            volatility60=float(row["volatility60"]),
            spread_bps=float(row["spread_bps"]),
        ))
    return signals


def effective_entry(ask: float, policy: engine.CostPolicy) -> float:
    return ask * (1.0 + policy.slippage_bps_per_side / engine.BPS)


def effective_exit(bid: float, policy: engine.CostPolicy) -> float:
    return bid * (1.0 - policy.slippage_bps_per_side / engine.BPS)


def canonical_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def default_output(started: datetime) -> Path:
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    return Path("evidence") / "v56" / f"paper_hour_{stamp}.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="v5.6 cost-aware paper hour")
    parser.add_argument("--duration", type=int, default=3600)
    parser.add_argument("--poll", type=int, default=60)
    parser.add_argument("--capital-inr", type=float, default=10_000.0)
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in engine.ExecutionMode],
        default=engine.ExecutionMode.LOSS_AVERSE_PAPER.value,
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.duration < 0 or args.poll <= 0 or args.capital_inr <= 0.0:
        raise ValueError("duration, poll and capital must be positive")
    started = utc_now()
    policy = engine.CostPolicy(max_hold_seconds=args.duration)
    mode = engine.ExecutionMode(args.mode)
    signals = build_signals(started)
    decision = engine.choose_entry(signals, policy, mode)
    selected = decision.selected_asset
    output_path = args.output or default_output(started)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    entry_quote: dict[str, float] | None = None
    entry_price: float | None = None
    observations: list[dict[str, Any]] = []
    exit_action = "NO_TRADE"
    exit_quote: dict[str, float] | None = None
    exit_price: float | None = None
    net = 0.0

    if selected is not None:
        entry_quote = ticker(PRODUCTS[selected])
        entry_price = effective_entry(entry_quote["ask"], policy)
        peak_bid = effective_exit(entry_quote["bid"], policy)
        deadline = time.monotonic() + args.duration
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                elapsed = args.duration
            else:
                time.sleep(min(args.poll, remaining))
                elapsed = args.duration - max(0, int(deadline - time.monotonic()))
            quote = ticker(PRODUCTS[selected])
            current_bid = effective_exit(quote["bid"], policy)
            peak_bid = max(peak_bid, current_bid)
            exit_decision = engine.evaluate_exit(
                engine.PositionState(selected, entry_price, peak_bid),
                current_bid,
                elapsed,
                policy,
            )
            mark = {
                "time_utc": iso(utc_now()),
                "bid": quote["bid"],
                "ask": quote["ask"],
                "effective_exit_bid": current_bid,
                "net_return": engine.net_return(entry_price, current_bid, policy),
                "exit_action": exit_decision.action,
            }
            observations.append(mark)
            if exit_decision.action != "HOLD":
                exit_action = exit_decision.action
                exit_quote = quote
                exit_price = current_bid
                net = mark["net_return"]
                break

    ended = utc_now()
    allocated_capital = args.capital_inr * decision.allocation
    pnl = allocated_capital * net
    result: dict[str, Any] = {
        "schema_version": "v56-cost-aware-paper-hour-1",
        "paper_only": True,
        "authorizes_trading": False,
        "started_at_utc": iso(started),
        "ended_at_utc": iso(ended),
        "duration_seconds_requested": args.duration,
        "paper_capital_inr": args.capital_inr,
        "allocated_capital_inr": allocated_capital,
        "mode": mode.value,
        "policy": asdict(policy),
        "signals": [asdict(value) for value in signals],
        "decision": asdict(decision),
        "selected_asset": selected,
        "entry_quote": entry_quote,
        "effective_entry_ask": entry_price,
        "exit_action": exit_action,
        "exit_quote": exit_quote,
        "effective_exit_bid": exit_price,
        "net_return_on_position": net,
        "profit_or_loss_inr": pnl,
        "outcome": "CASH" if selected is None else "PROFIT" if pnl > 0 else "LOSS" if pnl < 0 else "FLAT",
        "observations": observations,
    }
    result["result_sha256"] = canonical_hash(result)
    output_path.write_text(
        json.dumps(result, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
