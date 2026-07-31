from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import historical_monthly_ensemble_v30 as v30

EXECUTION_POLICY = (
    "fixed_recovery_exit_trend_regime_separation_natural_drift_costed_next_open"
)


def _trend_mode(
    model: v30.ModelSpec,
    features: dict[str, v30.Features],
) -> bool:
    btc = features["BTC"]
    flags = {
        asset: (
            item.close > v30._trend_sma(item, model.trend_sma)
            and item.return_20 > 0.0
            and item.return_60 > 0.0
        )
        for asset, item in features.items()
    }
    return (
        btc.close > v30._trend_sma(btc, model.trend_sma)
        and btc.return_20 > 0.0
        and btc.return_60 > 0.0
        and sum(flags.values()) / len(v30.ASSETS) >= 1.0 / 3.0
    )


def guarded_target(
    model: v30.ModelSpec,
    features: dict[str, v30.Features],
    previous_assets: tuple[str, ...],
    previous_sleeve: str,
    trend_age: int,
    recovery_days_left: int,
    brake_active: bool,
) -> tuple[dict[str, float], tuple[str, ...], str, int, int]:
    if brake_active:
        return {}, (), "cash", 0, 0
    if previous_sleeve == "recovery" and recovery_days_left == 0:
        return {}, (), "cash", 0, 0

    result = v30._target(
        model,
        features,
        previous_assets,
        previous_sleeve,
        trend_age,
        recovery_days_left,
        brake_active,
    )
    if _trend_mode(model, features) and result[2] != "trend":
        return {}, (), "cash", 0, 0
    return result


def simulate_guarded(
    model: v30.ModelSpec,
    bars: dict[str, dict[v30.datetime, v30.v25.HourlyBar]],
    features: dict[v30.datetime, dict[str, v30.Features]],
    start: v30.datetime,
    end: v30.datetime,
    cost: float,
) -> v30.SimulationResult:
    current_weights: dict[str, float] = {}
    selected_assets: tuple[str, ...] = ()
    sleeve = "cash"
    trend_age = 0
    recovery_days_left = 0
    brake_active = False
    daily_returns: list[float] = []
    gross_returns: list[float] = []
    turnover_total = 0.0
    action_days = 0
    used_assets: set[str] = set()
    used_sleeves: set[str] = set()
    asset_contribution = {asset: 0.0 for asset in v30.ASSETS}
    sleeve_contribution: dict[str, float] = {}
    day = start

    while day <= end:
        if (
            not brake_active
            and v30._compounded(daily_returns) <= v30.MONTHLY_LOSS_BRAKE
        ):
            brake_active = True
        signal_day = day - v30.timedelta(days=1)
        next_day = day + v30.timedelta(days=1)
        if signal_day not in features:
            raise v30.HistoricalMonthlyEnsembleV30Error(
                f"Features unavailable for {v30._utc(signal_day)}"
            )
        (
            target,
            selected_assets,
            next_sleeve,
            trend_age,
            recovery_days_left,
        ) = guarded_target(
            model,
            features[signal_day],
            selected_assets,
            sleeve,
            trend_age,
            recovery_days_left,
            brake_active,
        )
        exposure = sum(target.values())
        if (
            exposure > model.maximum_exposure + 1e-12
            or exposure > 0.20 + 1e-12
        ):
            raise v30.HistoricalMonthlyEnsembleV30Error(
                "Target exposure cap violated"
            )

        turnover_by_asset = {
            asset: abs(
                target.get(asset, 0.0) - current_weights.get(asset, 0.0)
            )
            for asset in v30.ASSETS
        }
        turnover = sum(turnover_by_asset.values())
        trading_cost = 0.5 * cost * turnover
        if turnover > 1e-10 and target:
            action_days += 1
        turnover_total += turnover
        used_assets.update(target)
        if target:
            used_sleeves.add(next_sleeve)

        gross = 0.0
        per_asset: dict[str, float] = {}
        for asset, weight in target.items():
            raw = (
                bars[asset][next_day].open / bars[asset][day].open - 1.0
            )
            contribution = weight * raw
            per_asset[asset] = contribution
            gross += contribution
        net = gross - trading_cost
        gross_returns.append(gross)
        daily_returns.append(net)

        traded_total = sum(turnover_by_asset.values())
        for asset in v30.ASSETS:
            allocated_cost = (
                trading_cost * turnover_by_asset[asset] / traded_total
                if traded_total > 0.0
                else 0.0
            )
            asset_contribution[asset] += (
                per_asset.get(asset, 0.0) - allocated_cost
            )
        sleeve_contribution[next_sleeve] = (
            sleeve_contribution.get(next_sleeve, 0.0) + net
        )

        denominator = 1.0 + net
        if denominator <= 0.0:
            raise v30.HistoricalMonthlyEnsembleV30Error(
                "Portfolio equity became nonpositive"
            )
        current_weights = {
            asset: weight
            * (bars[asset][next_day].open / bars[asset][day].open)
            / denominator
            for asset, weight in target.items()
        }
        sleeve = next_sleeve
        day += v30.timedelta(days=1)

    final_turnover = sum(current_weights.values())
    if final_turnover > 1e-12:
        final_cost = 0.5 * cost * final_turnover
        daily_returns.append(-final_cost)
        gross_returns.append(0.0)
        turnover_total += final_turnover
        for asset, weight in current_weights.items():
            asset_contribution[asset] -= (
                final_cost * weight / final_turnover
            )
        sleeve_contribution["final_liquidation"] = -final_cost

    return v30.SimulationResult(
        net_return=v30._compounded(daily_returns),
        gross_return=v30._compounded(gross_returns),
        maximum_drawdown=v30._maximum_drawdown(daily_returns),
        turnover=turnover_total,
        non_cash_action_days=action_days,
        selected_assets=sorted(used_assets),
        active_sleeves=sorted(used_sleeves),
        daily_returns=daily_returns,
        asset_contribution={
            key: value
            for key, value in sorted(asset_contribution.items())
            if abs(value) > 1e-15
        },
        sleeve_contribution=dict(sorted(sleeve_contribution.items())),
        brake_triggered=brake_active,
    )


def run_guarded_ensemble(max_workers: int = 20) -> dict[str, Any]:
    original = v30.simulate
    v30.simulate = simulate_guarded
    try:
        report = v30.run_ensemble(max_workers=max_workers)
    finally:
        v30.simulate = original
    report["execution_policy"] = EXECUTION_POLICY
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
        description="Run guarded v3.0 monthly trend-recovery ensemble."
    )
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--max-workers", type=int, default=20)
    args = parser.parse_args(argv)
    report = run_guarded_ensemble(max_workers=args.max_workers)
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
                "standard_months": verification["standard"][
                    "window_returns"
                ],
                "stress_months": verification["stress"][
                    "window_returns"
                ],
                "action_days": verification["standard"][
                    "window_action_days"
                ],
                "maximum_drawdown": verification["standard"][
                    "maximum_drawdown"
                ],
                "execution_policy": report["execution_policy"],
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
