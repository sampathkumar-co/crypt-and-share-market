from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import historical_rotation_v28 as v28

ACCOUNTING_POLICY = "independent_windows_start_and_end_in_cash"
TARGET_POLICY = "exact_configured_cadence_with_daily_30pct_exposure_cap"


def _guarded_daily_target(
    model: v28.ModelSpec,
    signal_features: dict[str, v28.AssetFeatures],
    prior_weights: dict[str, float],
    prior_sleeve: str,
    days_since_trend_rebalance: int,
) -> tuple[dict[str, float], str, int]:
    effective_age = days_since_trend_rebalance
    if (
        prior_sleeve == "trend"
        and days_since_trend_rebalance >= model.rebalance_days - 1
    ):
        effective_age = model.rebalance_days
    weights, sleeve, age = v28._daily_target(
        model,
        signal_features,
        prior_weights,
        prior_sleeve,
        effective_age,
    )
    exposure = sum(weights.values())
    if exposure > v28.MAX_EXPOSURE + 1e-12:
        scale = v28.MAX_EXPOSURE / exposure
        weights = {asset: weight * scale for asset, weight in weights.items()}
    return weights, sleeve, age


def simulate_closed(
    model: v28.ModelSpec,
    bars: dict[str, dict[v28.datetime, v28.v25.HourlyBar]],
    features: dict[v28.datetime, dict[str, v28.AssetFeatures]],
    start: v28.datetime,
    end: v28.datetime,
    cost: float,
) -> v28.SimulationResult:
    current_weights: dict[str, float] = {}
    current_sleeve = "cash"
    days_since_trend_rebalance = 0
    daily_returns: list[float] = []
    gross_returns: list[float] = []
    turnover_total = 0.0
    action_days = 0
    selected_assets: set[str] = set()
    active_sleeves: set[str] = set()
    asset_contribution = {asset: 0.0 for asset in v28.ASSETS}
    sleeve_contribution: dict[str, float] = {}
    day = start
    while day <= end:
        signal_day = day - v28.timedelta(days=1)
        next_day = day + v28.timedelta(days=1)
        if signal_day not in features:
            raise v28.HistoricalRotationV28Error(
                f"Features unavailable for {v28._utc(signal_day)}"
            )
        target, sleeve, days_since_trend_rebalance = _guarded_daily_target(
            model,
            features[signal_day],
            current_weights,
            current_sleeve,
            days_since_trend_rebalance,
        )
        if sum(target.values()) > v28.MAX_EXPOSURE + 1e-12:
            raise v28.HistoricalRotationV28Error("Target exposure exceeds 30%")
        traded = {
            asset: abs(target.get(asset, 0.0) - current_weights.get(asset, 0.0))
            for asset in v28.ASSETS
        }
        turnover = sum(traded.values())
        trading_cost = 0.5 * cost * turnover
        if turnover > 1e-10 and target:
            action_days += 1
        turnover_total += turnover
        selected_assets.update(target)
        if target:
            active_sleeves.add(sleeve)
        gross = 0.0
        per_asset_gross: dict[str, float] = {}
        for asset, weight in target.items():
            entry = bars[asset][day].open
            exit_price = bars[asset][next_day].open
            value = weight * (exit_price / entry - 1.0)
            per_asset_gross[asset] = value
            gross += value
        net = gross - trading_cost
        gross_returns.append(gross)
        daily_returns.append(net)
        traded_total = sum(traded.values())
        for asset in v28.ASSETS:
            allocated_cost = (
                trading_cost * traded[asset] / traded_total
                if traded_total > 0.0
                else 0.0
            )
            asset_contribution[asset] += (
                per_asset_gross.get(asset, 0.0) - allocated_cost
            )
        sleeve_contribution[sleeve] = (
            sleeve_contribution.get(sleeve, 0.0) + net
        )
        denominator = 1.0 + net
        if denominator <= 0.0:
            raise v28.HistoricalRotationV28Error(
                "Portfolio equity became nonpositive"
            )
        drifted: dict[str, float] = {}
        for asset, weight in target.items():
            entry = bars[asset][day].open
            exit_price = bars[asset][next_day].open
            drifted[asset] = weight * (exit_price / entry) / denominator
        current_weights = drifted
        current_sleeve = sleeve
        day += v28.timedelta(days=1)

    final_turnover = sum(current_weights.values())
    if final_turnover > 1e-12:
        final_cost = 0.5 * cost * final_turnover
        daily_returns.append(-final_cost)
        gross_returns.append(0.0)
        turnover_total += final_turnover
        for asset, weight in current_weights.items():
            asset_contribution[asset] -= final_cost * weight / final_turnover
        sleeve_contribution["final_liquidation"] = -final_cost

    return v28.SimulationResult(
        net_return=v28._compounded(daily_returns),
        gross_return=v28._compounded(gross_returns),
        maximum_drawdown=v28._maximum_drawdown(daily_returns),
        turnover=turnover_total,
        non_cash_action_days=action_days,
        selected_assets=sorted(selected_assets),
        active_sleeves=sorted(active_sleeves),
        daily_returns=daily_returns,
        asset_contribution={
            key: value
            for key, value in sorted(asset_contribution.items())
            if abs(value) > 1e-15
        },
        sleeve_contribution=dict(sorted(sleeve_contribution.items())),
    )


def run_guarded_rotation(max_workers: int = 16) -> dict[str, Any]:
    original = v28.simulate
    v28.simulate = simulate_closed
    try:
        report = v28.run_rotation(max_workers=max_workers)
    finally:
        v28.simulate = original
    report["accounting_policy"] = ACCOUNTING_POLICY
    report["target_policy"] = TARGET_POLICY
    fingerprints = dict(report["fingerprints"])
    fingerprints["runner_sha256"] = hashlib.sha256(
        Path(__file__).resolve().read_bytes()
    ).hexdigest()
    report["fingerprints"] = fingerprints
    report.pop("report_sha256", None)
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
        description="Run guarded v2.8 regime-adaptive rotation research."
    )
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--max-workers", type=int, default=16)
    args = parser.parse_args(argv)
    report = run_guarded_rotation(max_workers=args.max_workers)
    _atomic_json(Path(args.json_out), report)
    verification = report["verification"]
    print(
        json.dumps(
            {
                "status": report["screening_status"],
                "chosen_model": report["chosen_model"],
                "standard_return": verification["standard"][
                    "net_compounded_return"
                ],
                "stress_return": verification["stress"][
                    "net_compounded_return"
                ],
                "standard_quarters": verification["standard"][
                    "window_returns"
                ],
                "stress_quarters": verification["stress"][
                    "window_returns"
                ],
                "action_days": verification["standard"][
                    "window_action_days"
                ],
                "maximum_drawdown": verification["standard"][
                    "maximum_drawdown"
                ],
                "accounting_policy": report["accounting_policy"],
                "target_policy": report["target_policy"],
                "report_sha256": report["report_sha256"],
                "authorizes_trading": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
