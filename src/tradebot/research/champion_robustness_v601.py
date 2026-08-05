from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import common_accounting_tournament_v60 as tournament
from tradebot.research import historical_coinbase_replication_v32 as v32
from tradebot.research import historical_proxy_screen_v25 as v25
from tradebot.research import historical_yield_trend_integrity_v312 as v312
from tradebot.research import historical_yield_trend_scheduled_execution_v312 as execution
from tradebot.research import historical_yield_trend_v31 as v31
from tradebot.research import historical_yield_trend_v311_transport as cash_transport


SCHEMA_VERSION = "6.0.1-champion-delay-concentration"
CONTRACT_PATH = Path("research/V601_CHAMPION_ROBUSTNESS_IMPLEMENTATION_CONTRACT.md")


class ChampionRobustnessV601Error(RuntimeError):
    """Raised when the unchanged champion cannot be diagnosed exactly."""


@dataclass(frozen=True)
class DiagnosticSimulation:
    net_return: float
    cash_return: float
    excess_return: float
    maximum_drawdown: float
    action_days: int
    daily_returns: tuple[float, ...]
    interval_excess_contributions: tuple[float, ...]
    maximum_positive_interval_share: float


def _compounded(values: list[float] | tuple[float, ...]) -> float:
    equity = 1.0
    for value in values:
        equity *= 1.0 + value
    return equity - 1.0


def _maximum_drawdown(values: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in values:
        equity *= 1.0 + value
        peak = max(peak, equity)
        worst = max(worst, 1.0 - equity / peak)
    return worst


def _positive_concentration(values: list[float]) -> float:
    positive = [value for value in values if value > 0.0]
    total = sum(positive)
    if total <= 0.0:
        return 1.0
    return max(positive) / total


def simulate_diagnostic(
    model: v31.ModelSpec,
    bars: Mapping[str, Mapping[datetime, v25.HourlyBar]],
    features: Mapping[datetime, Mapping[str, v31.Features]],
    cash_returns: Mapping[datetime, float],
    start: datetime,
    end: datetime,
    cost: float,
    *,
    signal_lag_days: int,
) -> DiagnosticSimulation:
    if signal_lag_days < 1:
        raise ChampionRobustnessV601Error("signal lag must be at least one day")

    current_weights: dict[str, float] = {}
    selected_assets: tuple[str, ...] = ()
    sleeve = "cash"
    age = 0
    daily_returns: list[float] = []
    cash_benchmark_returns: list[float] = []
    action_days = 0

    interval_active = False
    interval_strategy_equity = 1.0
    interval_cash_equity = 1.0
    interval_contributions: list[float] = []

    def close_interval() -> None:
        nonlocal interval_active, interval_strategy_equity, interval_cash_equity
        if interval_active:
            interval_contributions.append(
                (interval_strategy_equity - 1.0)
                - (interval_cash_equity - 1.0)
            )
        interval_active = False
        interval_strategy_equity = 1.0
        interval_cash_equity = 1.0

    day = start
    while day <= end:
        signal_day = day - timedelta(days=signal_lag_days)
        next_day = day + timedelta(days=1)
        if signal_day not in features:
            raise ChampionRobustnessV601Error(
                f"features unavailable for {v31._utc(signal_day)}"
            )

        prior_selected = selected_assets
        prior_sleeve = sleeve
        prior_age = age
        proposed, proposed_selected, proposed_sleeve, proposed_age = v31._target(
            model,
            features[signal_day],
            selected_assets,
            sleeve,
            age,
        )
        scheduled_due = (
            prior_sleeve != "trend"
            or prior_age >= model.rebalance_days - 1
        )
        if proposed_sleeve == "cash":
            target: dict[str, float] = {}
            selected_assets = ()
            sleeve = "cash"
            age = 0
            trade_decision = bool(current_weights)
        elif scheduled_due:
            target = dict(proposed)
            selected_assets = proposed_selected
            sleeve = proposed_sleeve
            age = proposed_age
            trade_decision = True
        else:
            if proposed_selected != prior_selected:
                raise ChampionRobustnessV601Error(
                    "selected assets changed before scheduled rebalance"
                )
            target = dict(current_weights)
            selected_assets = prior_selected
            sleeve = prior_sleeve
            age = proposed_age
            trade_decision = False

        target_exposure = sum(target.values())
        if scheduled_due or proposed_sleeve == "cash":
            if (
                target_exposure > model.maximum_exposure + 1e-12
                or target_exposure > 0.20 + 1e-12
            ):
                raise ChampionRobustnessV601Error("target exposure cap violated")

        turnover = sum(
            abs(target.get(asset, 0.0) - current_weights.get(asset, 0.0))
            for asset in v31.ASSETS
        )
        if not trade_decision and turnover > 1e-12:
            raise ChampionRobustnessV601Error(
                "non-due trend day generated turnover"
            )
        trading_cost = 0.5 * cost * turnover
        action = turnover > 1e-10
        if action:
            close_interval()
            interval_active = True
            action_days += 1

        cash_return = float(cash_returns[day])
        cash_weight = 1.0 - target_exposure
        if cash_weight < -1e-12:
            raise ChampionRobustnessV601Error(
                "naturally drifted exposure exceeded portfolio equity"
            )
        day_crypto = 0.0
        for asset, weight in target.items():
            raw = bars[asset][next_day].open / bars[asset][day].open - 1.0
            day_crypto += weight * raw
        net = cash_weight * cash_return + day_crypto - trading_cost
        daily_returns.append(net)
        cash_benchmark_returns.append(cash_return)
        if interval_active:
            interval_strategy_equity *= 1.0 + net
            interval_cash_equity *= 1.0 + cash_return

        denominator = 1.0 + net
        if denominator <= 0.0:
            raise ChampionRobustnessV601Error("portfolio equity became nonpositive")
        current_weights = {
            asset: weight
            * (bars[asset][next_day].open / bars[asset][day].open)
            / denominator
            for asset, weight in target.items()
        }
        day += timedelta(days=1)

    final_turnover = sum(current_weights.values())
    if final_turnover > 1e-12:
        final_cost = 0.5 * cost * final_turnover
        daily_returns.append(-final_cost)
        action_days += 1
        if not interval_active:
            interval_active = True
        interval_strategy_equity *= 1.0 - final_cost
    close_interval()

    net_return = _compounded(daily_returns)
    cash_return = _compounded(cash_benchmark_returns)
    return DiagnosticSimulation(
        net_return=net_return,
        cash_return=cash_return,
        excess_return=net_return - cash_return,
        maximum_drawdown=_maximum_drawdown(daily_returns),
        action_days=action_days,
        daily_returns=tuple(daily_returns),
        interval_excess_contributions=tuple(interval_contributions),
        maximum_positive_interval_share=_positive_concentration(
            interval_contributions
        ),
    )


def _load_binance() -> tuple[
    dict[str, dict[datetime, v25.HourlyBar]],
    dict[datetime, dict[str, v31.Features]],
    dict[datetime, float],
]:
    original_downloader = v31._download_fred
    v31._download_fred = cash_transport.download_cash_series_with_resilience
    try:
        downloaded, normalized_cash, _ = v31.download_inputs(max_workers=16)
    finally:
        v31._download_fred = original_downloader
    bars, dates = v31.assemble_bars(downloaded)
    features = v31.build_features(bars, dates)
    rates = cash_transport.parse_fred_rates(normalized_cash)
    return bars, features, v31.build_daily_cash_returns(rates, dates)


def _load_coinbase() -> tuple[
    dict[str, dict[datetime, v25.HourlyBar]],
    dict[datetime, dict[str, v31.Features]],
    dict[datetime, float],
]:
    bars, _ = v32.download_coinbase_bars()
    normalized_cash, _ = cash_transport.download_cash_series_with_resilience()
    dates = v32._days(v32.DATA_START, v32.EXIT_DATE)
    features = v31.build_features(bars, dates)
    rates = cash_transport.parse_fred_rates(normalized_cash)
    return bars, features, v31.build_daily_cash_returns(rates, dates)


def _source_diagnostics(
    name: str,
    bars: Mapping[str, Mapping[datetime, v25.HourlyBar]],
    features: Mapping[datetime, Mapping[str, v31.Features]],
    cash_returns: Mapping[datetime, float],
    authoritative: Mapping[str, Any],
) -> dict[str, Any]:
    control: dict[str, DiagnosticSimulation] = {}
    stress: dict[str, DiagnosticSimulation] = {}
    delayed: dict[str, DiagnosticSimulation] = {}
    for period in v31.VERIFICATION_PERIODS:
        control[period.name] = simulate_diagnostic(
            v312.FROZEN_MODEL,
            bars,
            features,
            cash_returns,
            period.start,
            period.end,
            v31.STANDARD_COST,
            signal_lag_days=1,
        )
        stress[period.name] = simulate_diagnostic(
            v312.FROZEN_MODEL,
            bars,
            features,
            cash_returns,
            period.start,
            period.end,
            v31.STRESS_COST,
            signal_lag_days=1,
        )
        delayed[period.name] = simulate_diagnostic(
            v312.FROZEN_MODEL,
            bars,
            features,
            cash_returns,
            period.start,
            period.end,
            v31.STANDARD_COST,
            signal_lag_days=2,
        )

    for period in v31.VERIFICATION_PERIODS:
        key = period.name
        expected_standard = float(authoritative["standard"]["window_returns"][key])
        expected_stress = float(authoritative["stress"]["window_returns"][key])
        expected_cash = float(authoritative["standard"]["cash_window_returns"][key])
        expected_actions = int(authoritative["standard"]["window_action_days"][key])
        if abs(control[key].net_return - expected_standard) > 1e-12:
            raise ChampionRobustnessV601Error(
                f"{name} standard control mismatch in {key}"
            )
        if abs(stress[key].net_return - expected_stress) > 1e-12:
            raise ChampionRobustnessV601Error(
                f"{name} stress control mismatch in {key}"
            )
        if abs(control[key].cash_return - expected_cash) > 1e-12:
            raise ChampionRobustnessV601Error(
                f"{name} cash control mismatch in {key}"
            )
        if control[key].action_days != expected_actions:
            raise ChampionRobustnessV601Error(
                f"{name} action control mismatch in {key}"
            )

    control_returns = [control[p.name].net_return for p in v31.VERIFICATION_PERIODS]
    stress_returns = [stress[p.name].net_return for p in v31.VERIFICATION_PERIODS]
    delayed_returns = [delayed[p.name].net_return for p in v31.VERIFICATION_PERIODS]
    cash_window_returns = [control[p.name].cash_return for p in v31.VERIFICATION_PERIODS]
    intervals = [
        value
        for period in v31.VERIFICATION_PERIODS
        for value in control[period.name].interval_excess_contributions
    ]
    control_compounded = _compounded(control_returns)
    stress_compounded = _compounded(stress_returns)
    delayed_compounded = _compounded(delayed_returns)
    cash_compounded = _compounded(cash_window_returns)
    expected_control = float(authoritative["standard"]["net_compounded_return"])
    expected_stress = float(authoritative["stress"]["net_compounded_return"])
    expected_drawdown = float(authoritative["standard"]["maximum_drawdown"])
    observed_drawdown = max(value.maximum_drawdown for value in control.values())
    if abs(control_compounded - expected_control) > 1e-12:
        raise ChampionRobustnessV601Error(f"{name} compounded control mismatch")
    if abs(stress_compounded - expected_stress) > 1e-12:
        raise ChampionRobustnessV601Error(f"{name} compounded stress mismatch")
    if abs(observed_drawdown - expected_drawdown) > 1e-12:
        raise ChampionRobustnessV601Error(f"{name} drawdown control mismatch")

    return {
        "source": name,
        "control_exact": True,
        "control_standard_return": control_compounded,
        "control_stress_return": stress_compounded,
        "cash_return": cash_compounded,
        "delayed_standard_return": delayed_compounded,
        "delayed_excess_over_cash": delayed_compounded - cash_compounded,
        "delayed_window_returns": {
            period.name: delayed[period.name].net_return
            for period in v31.VERIFICATION_PERIODS
        },
        "decision_interval_count": len(intervals),
        "positive_decision_interval_count": sum(value > 0.0 for value in intervals),
        "maximum_positive_decision_interval_share": _positive_concentration(intervals),
        "control_action_days": sum(value.action_days for value in control.values()),
    }


def build_report(
    v312_report: Mapping[str, Any],
    v32_report: Mapping[str, Any],
) -> dict[str, Any]:
    tournament._validate_report_sha(
        v312_report, tournament.EXPECTED_V312_SHA256, "v3.1.2"
    )
    tournament._validate_report_sha(
        v32_report, tournament.EXPECTED_V32_SHA256, "v3.2"
    )
    if (
        v32_report.get("v312_dependency_report_sha256")
        != tournament.EXPECTED_V312_DEPENDENCY_SHA256
    ):
        raise ChampionRobustnessV601Error("v3.2 dependency link changed")
    if not CONTRACT_PATH.is_file():
        raise ChampionRobustnessV601Error("robustness contract is missing")

    binance = _source_diagnostics(
        "binance",
        *_load_binance(),
        v312_report,
    )
    coinbase = _source_diagnostics(
        "coinbase",
        *_load_coinbase(),
        v32_report,
    )
    conservative_delay = min(
        float(binance["delayed_excess_over_cash"]),
        float(coinbase["delayed_excess_over_cash"]),
    )
    conservative_concentration = max(
        float(binance["maximum_positive_decision_interval_share"]),
        float(coinbase["maximum_positive_decision_interval_share"]),
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_continuous_paper": False,
        "contract_sha256": hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest(),
        "source_reports": {
            "binance": tournament.EXPECTED_V312_SHA256,
            "coinbase": tournament.EXPECTED_V32_SHA256,
            "original_binance_dependency": (
                tournament.EXPECTED_V312_DEPENDENCY_SHA256
            ),
        },
        "sources": {
            "binance": binance,
            "coinbase": coinbase,
        },
        "conservative": {
            "delayed_excess_over_cash": conservative_delay,
            "maximum_positive_decision_interval_share": conservative_concentration,
            "delay_gate_passed": conservative_delay > 0.0,
            "trade_concentration_gate_passed": conservative_concentration <= 0.20,
        },
        "status": (
            "CHAMPION_ROBUSTNESS_PASSED"
            if conservative_delay > 0.0 and conservative_concentration <= 0.20
            else "CHAMPION_ROBUSTNESS_FAILED"
        ),
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run v6.0.1 champion delay and concentration diagnostics"
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
    print(json.dumps({
        "status": report["status"],
        "conservative": report["conservative"],
        "report_sha256": report["report_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
