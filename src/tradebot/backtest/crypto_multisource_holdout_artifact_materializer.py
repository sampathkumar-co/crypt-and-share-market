from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from typing import Any

from tradebot.backtest import crypto_multisource_holdout as frozen
from tradebot.backtest.crypto_multisource_holdout_reporting_hotfix import (
    evaluate_multisource_holdout_reporting_hotfix,
)
from tradebot.models import Market


def add_derived_display_fields(payload: dict[str, Any]) -> dict[str, Any]:
    variants = {
        item["variant"]: item
        for item in payload.get("variants", [])
        if isinstance(item, dict) and "variant" in item
    }
    primary = variants.get("primary_multisource")
    raw = variants.get("raw_simple_trend")
    if primary is None or raw is None:
        raise ValueError("Frozen primary or raw variant is missing")
    periods = primary.get("periods", [])
    payload["primary_average_improvement_vs_raw"] = (
        float(primary["average_return"]) - float(raw["average_return"])
    )
    payload["primary_beats_raw_periods"] = round(
        float(payload["primary_beats_raw_fraction"]) * len(periods)
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Upload-ready materialisation of the consumed v1.3.1 holdout"
    )
    parser.add_argument("--price-folder", required=True)
    parser.add_argument("--external-folder", required=True)
    parser.add_argument(
        "--market",
        choices=[market.value for market in Market],
        default=Market.CRYPTO.value,
    )
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args(argv)
    report = evaluate_multisource_holdout_reporting_hotfix(
        args.price_folder,
        args.external_folder,
        Market(args.market),
    )
    payload = add_derived_display_fields(asdict(report))
    frozen._write_json(args.json_out, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
