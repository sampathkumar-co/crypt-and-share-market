from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import champion_robustness_v601 as base
from tradebot.research import historical_coinbase_replication_v32 as v32
from tradebot.research import historical_yield_trend_v31 as v31
from tradebot.research import historical_yield_trend_v311_transport as cash_transport


ADDENDUM_PATH = Path("research/V6011_COINBASE_DELAY_WARMUP_ADDENDUM.md")
SCHEMA_VERSION = "6.0.1.1-champion-delay-real-warmup"
_WARMUP_INVENTORY: list[dict[str, Any]] = []


def _normalized_bar_payload(asset: str, bar: Any) -> dict[str, Any]:
    return {
        "asset": asset,
        "date": bar.hour.date().isoformat(),
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "quote_volume": float(bar.quote_volume),
        "taker_buy_quote_volume": float(bar.taker_buy_quote_volume),
    }


def _load_coinbase_with_real_warmup():
    global _WARMUP_INVENTORY
    if not ADDENDUM_PATH.is_file():
        raise base.ChampionRobustnessV601Error(
            "Coinbase delay-warmup addendum is missing"
        )

    bars, _ = v32.download_coinbase_bars()
    warmup_day = v32.DATA_START - timedelta(days=1)
    inventory: list[dict[str, Any]] = []
    for asset in v32.ASSETS:
        product = v32.PRODUCTS[asset]
        url = v32._candle_url(product, warmup_day, warmup_day)
        content, raw_digest = v32._download_json(url)
        parsed = v32._parse_coinbase_candles(
            content,
            asset=asset,
            requested_start=warmup_day,
            requested_end=warmup_day,
        )
        if set(parsed) != {warmup_day}:
            raise base.ChampionRobustnessV601Error(
                f"Coinbase {asset} genuine warmup candle unavailable"
            )
        bar = parsed[warmup_day]
        if warmup_day in bars[asset] and bars[asset][warmup_day] != bar:
            raise base.ChampionRobustnessV601Error(
                f"Coinbase {asset} warmup conflicts with existing history"
            )
        bars[asset][warmup_day] = bar
        normalized = _normalized_bar_payload(asset, bar)
        inventory.append(
            {
                "key": f"coinbase-delay-warmup:{asset}:{warmup_day.date()}",
                "provider": "coinbase-exchange-public-rest",
                "product": product,
                "requested_date": warmup_day.date().isoformat(),
                "url": url,
                "raw_sha256": raw_digest,
                "normalized_sha256": hashlib.sha256(
                    canonical_json(normalized).encode("utf-8")
                ).hexdigest(),
                "normalized": normalized,
                "rows": 1,
            }
        )

    dates = v32._days(warmup_day, v32.EXIT_DATE)
    for asset in v32.ASSETS:
        missing = [day for day in dates if day not in bars[asset]]
        if missing:
            raise base.ChampionRobustnessV601Error(
                f"Coinbase {asset} diagnostic history missing "
                f"{len(missing)} days; first={missing[0].date()}"
            )
    normalized_cash, _ = cash_transport.download_cash_series_with_resilience()
    features = v31.build_features(bars, dates)
    rates = cash_transport.parse_fred_rates(normalized_cash)
    cash_returns = v31.build_daily_cash_returns(rates, dates)
    _WARMUP_INVENTORY = sorted(inventory, key=lambda item: item["key"])
    return bars, features, cash_returns


def build_report(v312_report, v32_report) -> dict[str, Any]:
    original_loader = base._load_coinbase
    base._load_coinbase = _load_coinbase_with_real_warmup
    try:
        report = base.build_report(v312_report, v32_report)
    finally:
        base._load_coinbase = original_loader

    report.pop("report_sha256", None)
    report["schema_version"] = SCHEMA_VERSION
    report["warmup_addendum_sha256"] = hashlib.sha256(
        ADDENDUM_PATH.read_bytes()
    ).hexdigest()
    report["sources"]["coinbase"]["diagnostic_warmup_inventory"] = list(
        _WARMUP_INVENTORY
    )
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run v6.0.1.1 champion robustness with real Coinbase warmup"
    )
    parser.add_argument("--v312-json", type=Path, required=True)
    parser.add_argument("--v32-json", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_report(
        json.loads(args.v312_json.read_text(encoding="utf-8")),
        json.loads(args.v32_json.read_text(encoding="utf-8")),
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "conservative": report["conservative"],
                "coinbase_warmup": report["sources"]["coinbase"][
                    "diagnostic_warmup_inventory"
                ],
                "report_sha256": report["report_sha256"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
