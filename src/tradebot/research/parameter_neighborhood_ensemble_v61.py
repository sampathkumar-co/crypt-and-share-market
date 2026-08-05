from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from math import log
from pathlib import Path
from statistics import median
from typing import Any, Mapping

import numpy as np

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import champion_chronology_audit_v603 as v603
from tradebot.research import champion_robustness_v601 as robustness_base
from tradebot.research import champion_robustness_v6011 as robustness_warmup
from tradebot.research import champion_statistical_gates_v602 as v602
from tradebot.research import common_accounting_tournament_v60 as tournament
from tradebot.research import historical_yield_trend_v31 as v31


SCHEMA_VERSION = "6.1-parameter-neighborhood-ensemble"
PROTOCOL_PATH = Path("research/V61_PARAMETER_NEIGHBORHOOD_ENSEMBLE_PROTOCOL.md")
MEMBERS = tuple(
    model
    for model in v31.MODEL_GRID
    if model.rebalance_days == 10
    and abs(model.maximum_exposure - 0.10) <= 1e-15
)
TRIAL_COUNT = 225
FROZEN_GRID_SHARPE_STD = 0.17603369374678823


class ParameterNeighborhoodV61Error(RuntimeError):
    """Raised when the fixed ensemble cannot be reproduced safely."""


@dataclass
class MemberState:
    current_weights: dict[str, float] = field(default_factory=dict)
    selected_assets: tuple[str, ...] = ()
    sleeve: str = "cash"
    age: int = 0


@dataclass(frozen=True)
class EnsembleSimulation:
    net_return: float
    cash_return: float
    excess_return: float
    maximum_drawdown: float
    action_days: int
    daily_returns: tuple[float, ...]
    cash_daily_returns: tuple[float, ...]
    interval_excess_contributions: tuple[float, ...]
    maximum_positive_interval_share: float


def _member_target(
    model: v31.ModelSpec,
    state: MemberState,
    features: Mapping[str, v31.Features],
) -> tuple[dict[str, float], tuple[str, ...], str, int, bool]:
    prior_selected = state.selected_assets
    prior_sleeve = state.sleeve
    prior_age = state.age
    proposed, proposed_selected, proposed_sleeve, proposed_age = v31._target(
        model,
        dict(features),
        prior_selected,
        prior_sleeve,
        prior_age,
    )
    scheduled_due = (
        prior_sleeve != "trend" or prior_age >= model.rebalance_days - 1
    )
    if proposed_sleeve == "cash":
        return {}, (), "cash", 0, bool(state.current_weights)
    if scheduled_due:
        return (
            dict(proposed),
            proposed_selected,
            proposed_sleeve,
            proposed_age,
            True,
        )
    if proposed_selected != prior_selected:
        raise ParameterNeighborhoodV61Error(
            f"{model.model_id} changed assets before scheduled rebalance"
        )
    return (
        dict(state.current_weights),
        prior_selected,
        prior_sleeve,
        proposed_age,
        False,
    )


def _drift_weights(
    target: Mapping[str, float],
    bars: Mapping[str, Mapping[datetime, Any]],
    day: datetime,
    next_day: datetime,
    cash_return: float,
    trading_cost: float,
) -> dict[str, float]:
    exposure = sum(target.values())
    cash_weight = 1.0 - exposure
    crypto_return = sum(
        weight * (bars[asset][next_day].open / bars[asset][day].open - 1.0)
        for asset, weight in target.items()
    )
    net = cash_weight * cash_return + crypto_return - trading_cost
    denominator = 1.0 + net
    if denominator <= 0.0:
        raise ParameterNeighborhoodV61Error("member or ensemble equity became nonpositive")
    return {
        asset: weight
        * (bars[asset][next_day].open / bars[asset][day].open)
        / denominator
        for asset, weight in target.items()
    }


def simulate_ensemble(
    bars: Mapping[str, Mapping[datetime, Any]],
    features: Mapping[datetime, Mapping[str, v31.Features]],
    cash_returns: Mapping[datetime, float],
    start: datetime,
    end: datetime,
    cost: float,
    *,
    signal_lag_days: int,
) -> EnsembleSimulation:
    if len(MEMBERS) != 16:
        raise ParameterNeighborhoodV61Error("frozen ensemble must contain 16 members")
    if signal_lag_days < 1:
        raise ParameterNeighborhoodV61Error("signal lag must be at least one day")

    states = {model.model_id: MemberState() for model in MEMBERS}
    ensemble_weights: dict[str, float] = {}
    daily_returns: list[float] = []
    cash_daily: list[float] = []
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
            raise ParameterNeighborhoodV61Error(
                f"features unavailable for {v31._utc(signal_day)}"
            )
        cash_return = float(cash_returns[day])
        member_targets: dict[str, dict[str, float]] = {}
        pending_states: dict[str, MemberState] = {}

        for model in MEMBERS:
            state = states[model.model_id]
            target, selected, sleeve, age, trade_decision = _member_target(
                model, state, features[signal_day]
            )
            exposure = sum(target.values())
            if exposure > 0.10 + 1e-12:
                raise ParameterNeighborhoodV61Error(
                    f"member exposure cap violated by {model.model_id}"
                )
            turnover = sum(
                abs(target.get(asset, 0.0) - state.current_weights.get(asset, 0.0))
                for asset in v31.ASSETS
            )
            if not trade_decision and turnover > 1e-12:
                raise ParameterNeighborhoodV61Error(
                    f"non-due turnover generated by {model.model_id}"
                )
            member_cost = 0.5 * cost * turnover
            pending_states[model.model_id] = MemberState(
                current_weights=_drift_weights(
                    target, bars, day, next_day, cash_return, member_cost
                ),
                selected_assets=selected,
                sleeve=sleeve,
                age=age,
            )
            member_targets[model.model_id] = target

        target = {
            asset: sum(
                member_targets[model.model_id].get(asset, 0.0)
                for model in MEMBERS
            )
            / len(MEMBERS)
            for asset in v31.ASSETS
        }
        target = {asset: weight for asset, weight in target.items() if weight > 1e-15}
        exposure = sum(target.values())
        if exposure > 0.10 + 1e-12:
            raise ParameterNeighborhoodV61Error("ensemble exposure cap violated")
        turnover = sum(
            abs(target.get(asset, 0.0) - ensemble_weights.get(asset, 0.0))
            for asset in v31.ASSETS
        )
        trading_cost = 0.5 * cost * turnover
        action = turnover > 1e-10
        if action:
            close_interval()
            interval_active = True
            action_days += 1

        cash_weight = 1.0 - exposure
        crypto_return = sum(
            weight * (bars[asset][next_day].open / bars[asset][day].open - 1.0)
            for asset, weight in target.items()
        )
        net = cash_weight * cash_return + crypto_return - trading_cost
        daily_returns.append(net)
        cash_daily.append(cash_return)
        if interval_active:
            interval_strategy_equity *= 1.0 + net
            interval_cash_equity *= 1.0 + cash_return
        ensemble_weights = _drift_weights(
            target, bars, day, next_day, cash_return, trading_cost
        )
        states = pending_states
        day += timedelta(days=1)

    final_turnover = sum(ensemble_weights.values())
    if final_turnover > 1e-12:
        final_cost = 0.5 * cost * final_turnover
        daily_returns.append(-final_cost)
        cash_daily.append(0.0)
        if not interval_active:
            interval_active = True
        interval_strategy_equity *= 1.0 - final_cost
    close_interval()

    net_return = robustness_base._compounded(daily_returns)
    cash_return = robustness_base._compounded(cash_daily)
    return EnsembleSimulation(
        net_return=net_return,
        cash_return=cash_return,
        excess_return=net_return - cash_return,
        maximum_drawdown=robustness_base._maximum_drawdown(daily_returns),
        action_days=action_days,
        daily_returns=tuple(daily_returns),
        cash_daily_returns=tuple(cash_daily),
        interval_excess_contributions=tuple(interval_contributions),
        maximum_positive_interval_share=robustness_base._positive_concentration(
            interval_contributions
        ),
    )


def _combine_periods(results: Mapping[str, EnsembleSimulation]) -> dict[str, Any]:
    ordered = [results[period.name] for period in v31.VERIFICATION_PERIODS]
    net_returns = [item.net_return for item in ordered]
    cash_returns = [item.cash_return for item in ordered]
    excess_returns = [item.excess_return for item in ordered]
    daily = [value for item in ordered for value in item.daily_returns]
    cash_daily = [value for item in ordered for value in item.cash_daily_returns]
    intervals = [
        value
        for item in ordered
        for value in item.interval_excess_contributions
    ]
    positive_years = [max(0.0, value) for value in excess_returns]
    positive_total = sum(positive_years)
    return {
        "net_compounded_return": robustness_base._compounded(net_returns),
        "cash_compounded_return": robustness_base._compounded(cash_returns),
        "excess_compounded_return": (
            robustness_base._compounded(net_returns)
            - robustness_base._compounded(cash_returns)
        ),
        "maximum_drawdown": robustness_base._maximum_drawdown(daily),
        "action_days": sum(item.action_days for item in ordered),
        "window_returns": {
            period.name: results[period.name].net_return
            for period in v31.VERIFICATION_PERIODS
        },
        "cash_window_returns": {
            period.name: results[period.name].cash_return
            for period in v31.VERIFICATION_PERIODS
        },
        "excess_window_returns": {
            period.name: results[period.name].excess_return
            for period in v31.VERIFICATION_PERIODS
        },
        "maximum_positive_year_share": (
            0.0 if positive_total <= 0.0 else max(positive_years) / positive_total
        ),
        "maximum_positive_interval_share": robustness_base._positive_concentration(
            intervals
        ),
        "daily_returns": daily,
        "cash_daily_returns": cash_daily,
        "relative_daily_returns": [
            (1.0 + strategy) / (1.0 + cash) - 1.0
            for strategy, cash in zip(daily, cash_daily, strict=True)
        ],
        "decision_interval_count": len(intervals),
        "positive_decision_interval_count": sum(value > 0.0 for value in intervals),
    }


def _source_evaluation(
    source: str,
    bars: Mapping[str, Mapping[datetime, Any]],
    features: Mapping[datetime, Mapping[str, v31.Features]],
    cash_returns: Mapping[datetime, float],
) -> dict[str, Any]:
    standard = {
        period.name: simulate_ensemble(
            bars,
            features,
            cash_returns,
            period.start,
            period.end,
            v31.STANDARD_COST,
            signal_lag_days=1,
        )
        for period in v31.VERIFICATION_PERIODS
    }
    stress = {
        period.name: simulate_ensemble(
            bars,
            features,
            cash_returns,
            period.start,
            period.end,
            v31.STRESS_COST,
            signal_lag_days=1,
        )
        for period in v31.VERIFICATION_PERIODS
    }
    delayed = {
        period.name: simulate_ensemble(
            bars,
            features,
            cash_returns,
            period.start,
            period.end,
            v31.STANDARD_COST,
            signal_lag_days=2,
        )
        for period in v31.VERIFICATION_PERIODS
    }
    standard_combined = _combine_periods(standard)
    stress_combined = _combine_periods(stress)
    delayed_combined = _combine_periods(delayed)
    return {
        "source": source,
        "standard": standard_combined,
        "stress": stress_combined,
        "delayed": delayed_combined,
        "standard_relative_series_sha256": hashlib.sha256(
            np.asarray(
                standard_combined["relative_daily_returns"], dtype="<f8"
            ).tobytes()
        ).hexdigest(),
    }


def _annualize(value: float, years: int = 5) -> float:
    return (1.0 + value) ** (1.0 / years) - 1.0


def _conservative(binance: Mapping[str, Any], coinbase: Mapping[str, Any]) -> dict[str, Any]:
    standard = min(
        float(binance["standard"]["net_compounded_return"]),
        float(coinbase["standard"]["net_compounded_return"]),
    )
    stress = min(
        float(binance["stress"]["net_compounded_return"]),
        float(coinbase["stress"]["net_compounded_return"]),
    )
    cash = max(
        float(binance["standard"]["cash_compounded_return"]),
        float(coinbase["standard"]["cash_compounded_return"]),
    )
    windows = {
        period.name: min(
            float(binance["standard"]["window_returns"][period.name]),
            float(coinbase["standard"]["window_returns"][period.name]),
        )
        for period in v31.VERIFICATION_PERIODS
    }
    stress_windows = {
        period.name: min(
            float(binance["stress"]["window_returns"][period.name]),
            float(coinbase["stress"]["window_returns"][period.name]),
        )
        for period in v31.VERIFICATION_PERIODS
    }
    return {
        "standard_return": standard,
        "stress_return": stress,
        "cash_return": cash,
        "annualized_standard_return": _annualize(standard),
        "annualized_cash_return": _annualize(cash),
        "annualized_excess_over_cash": _annualize(standard) - _annualize(cash),
        "standard_windows": windows,
        "stress_windows": stress_windows,
        "actions": min(
            int(binance["standard"]["action_days"]),
            int(coinbase["standard"]["action_days"]),
        ),
        "maximum_drawdown": max(
            float(binance["standard"]["maximum_drawdown"]),
            float(coinbase["standard"]["maximum_drawdown"]),
        ),
        "delayed_excess_over_cash": min(
            float(binance["delayed"]["excess_compounded_return"]),
            float(coinbase["delayed"]["excess_compounded_return"]),
        ),
        "maximum_positive_interval_share": max(
            float(binance["standard"]["maximum_positive_interval_share"]),
            float(coinbase["standard"]["maximum_positive_interval_share"]),
        ),
        "maximum_positive_year_share": max(
            float(binance["standard"]["maximum_positive_year_share"]),
            float(coinbase["standard"]["maximum_positive_year_share"]),
        ),
    }


def _rank_stability() -> dict[str, Any]:
    bars, features, cash_returns = robustness_base._load_binance()
    member_series = {
        model.model_id: v603._relative_series_for_model(
            model, bars, features, cash_returns
        )
        for model in MEMBERS
    }
    ensemble_parts: list[float] = []
    for period in v31.DISCOVERY_PERIODS:
        result = simulate_ensemble(
            bars,
            features,
            cash_returns,
            period.start,
            period.end,
            v31.STRESS_COST,
            signal_lag_days=1,
        )
        ensemble_parts.extend(
            (1.0 + strategy) / (1.0 + cash) - 1.0
            for strategy, cash in zip(
                result.daily_returns,
                result.cash_daily_returns,
                strict=True,
            )
        )
    series = dict(member_series)
    series["fixed-ensemble"] = np.asarray(ensemble_parts, dtype=float)
    lengths = {len(values) for values in series.values()}
    if len(lengths) != 1:
        raise ParameterNeighborhoodV61Error("rank audit series do not align")
    observations = lengths.pop()
    partitions = 8
    base, remainder = divmod(observations, partitions)
    blocks: list[np.ndarray] = []
    start = 0
    for index in range(partitions):
        stop = start + base + (1 if index < remainder else 0)
        blocks.append(np.arange(start, stop, dtype=int))
        start = stop
    percentiles: list[float] = []
    for chosen in itertools.combinations(range(1, partitions), 3):
        in_blocks = (0, *chosen)
        out_blocks = tuple(index for index in range(partitions) if index not in in_blocks)
        _ = np.concatenate([blocks[index] for index in in_blocks])
        out_indices = np.concatenate([blocks[index] for index in out_blocks])
        scores = {
            name: v603.annualized_sharpe(values[out_indices])
            for name, values in series.items()
        }
        ordered = sorted(scores, key=lambda name: (scores[name], name))
        rank = ordered.index("fixed-ensemble") + 1
        percentiles.append((rank - 0.5) / len(ordered))
    top_half_fraction = sum(value >= 0.5 for value in percentiles) / len(percentiles)
    median_percentile = float(median(percentiles))
    return {
        "members": 16,
        "candidates_including_ensemble": 17,
        "observations": observations,
        "evaluated_splits": len(percentiles),
        "top_half_fraction": top_half_fraction,
        "median_percentile_rank": median_percentile,
        "passed": top_half_fraction >= 0.80 and median_percentile >= 0.60,
        "percentile_ranks": percentiles,
    }


def build_report(
    v312_report: Mapping[str, Any],
    v32_report: Mapping[str, Any],
    v603_report: Mapping[str, Any],
) -> dict[str, Any]:
    tournament._validate_report_sha(
        v312_report, tournament.EXPECTED_V312_SHA256, "v3.1.2"
    )
    tournament._validate_report_sha(
        v32_report, tournament.EXPECTED_V32_SHA256, "v3.2"
    )
    v603_digest = tournament._validate_self_hashed_report(
        v603_report, "v6.0.3 chronology audit"
    )
    if v603_report.get("status") != "CHRONOLOGY_CORRECT_STATISTICS_FAILED":
        raise ParameterNeighborhoodV61Error("v6.0.3 blocker diagnosis changed")
    if not PROTOCOL_PATH.is_file() or len(MEMBERS) != 16:
        raise ParameterNeighborhoodV61Error("v6.1 protocol or member set invalid")

    binance = _source_evaluation("binance", *robustness_base._load_binance())
    coinbase = _source_evaluation(
        "coinbase", *robustness_warmup._load_coinbase_with_real_warmup()
    )
    conservative = _conservative(binance, coinbase)

    protocol_digest = hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()
    master_seed = int.from_bytes(
        hashlib.sha256(
            "|".join(
                [
                    tournament.EXPECTED_V312_SHA256,
                    tournament.EXPECTED_V32_SHA256,
                    v603_digest,
                    protocol_digest,
                ]
            ).encode("utf-8")
        ).digest()[:8],
        "big",
    )
    source_bootstrap: dict[str, list[dict[str, Any]]] = {}
    source_series: dict[str, np.ndarray] = {}
    for source_index, payload in enumerate((binance, coinbase)):
        source = str(payload["source"])
        values = np.asarray(
            payload["standard"]["relative_daily_returns"], dtype=float
        )
        source_series[source] = values
        source_bootstrap[source] = [
            asdict(
                v602.moving_block_bootstrap(
                    values,
                    block_length=block_length,
                    resamples=v602.BOOTSTRAP_RESAMPLES,
                    seed=master_seed + source_index * 10_000 + block_length,
                )
            )
            for block_length in v602.BLOCK_LENGTHS
        ]
    bootstrap_passed = all(
        bool(item["passed"])
        for rows in source_bootstrap.values()
        for item in rows
    )
    dsr = v603.deflated_sharpe_audit(
        source_series["binance"],
        source_series["coinbase"],
        trial_count=TRIAL_COUNT,
        sharpe_trial_std=FROZEN_GRID_SHARPE_STD,
    )
    stability = _rank_stability()

    material_gates = {
        "annualized_return_at_least_5pct": (
            conservative["annualized_standard_return"] >= 0.05
        ),
        "annualized_excess_over_cash_at_least_2pct": (
            conservative["annualized_excess_over_cash"] >= 0.02
        ),
        "positive_stress_return": conservative["stress_return"] > 0.0,
        "four_of_five_positive_standard_years": (
            sum(value > 0.0 for value in conservative["standard_windows"].values()) >= 4
        ),
        "three_of_five_positive_stress_years": (
            sum(value > 0.0 for value in conservative["stress_windows"].values()) >= 3
        ),
        "at_least_30_actions": conservative["actions"] >= 30,
        "drawdown_at_most_5pct": conservative["maximum_drawdown"] <= 0.05,
        "positive_delayed_excess": conservative["delayed_excess_over_cash"] > 0.0,
        "interval_concentration_at_most_20pct": (
            conservative["maximum_positive_interval_share"] <= 0.20
        ),
        "year_concentration_at_most_50pct": (
            conservative["maximum_positive_year_share"] <= 0.50
        ),
        "independent_sources_complete": True,
    }
    statistical_gates = {
        "both_sources_bootstrap_positive": bootstrap_passed,
        "direct_lineage_dsr": dsr.passed,
        "ensemble_rank_stability": bool(stability["passed"]),
    }
    passed = all(material_gates.values()) and all(statistical_gates.values())
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_continuous_paper": False,
        "retrospective_dates_exposed": True,
        "untouched_historical_dates": False,
        "protocol_sha256": protocol_digest,
        "v603_dependency_sha256": v603_digest,
        "members": [asdict(model) | {"model_id": model.model_id} for model in MEMBERS],
        "member_count": len(MEMBERS),
        "sources": {"binance": binance, "coinbase": coinbase},
        "conservative": conservative,
        "source_specific_bootstrap": source_bootstrap,
        "deflated_sharpe": asdict(dsr),
        "rank_stability": stability,
        "material_gates": material_gates,
        "statistical_gates": statistical_gates,
        "status": (
            "RETROSPECTIVE_ENSEMBLE_CANDIDATE_FORWARD_REQUIRED"
            if passed
            else "ENSEMBLE_REJECTED"
        ),
        "historical_breakthrough": False,
        "forward_breakthrough": False,
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run v6.1 fixed parameter ensemble")
    parser.add_argument("--v312-json", type=Path, required=True)
    parser.add_argument("--v32-json", type=Path, required=True)
    parser.add_argument("--v603-json", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_report(
        json.loads(args.v312_json.read_text(encoding="utf-8")),
        json.loads(args.v32_json.read_text(encoding="utf-8")),
        json.loads(args.v603_json.read_text(encoding="utf-8")),
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": report["status"],
        "conservative": report["conservative"],
        "material_gates": report["material_gates"],
        "statistical_gates": report["statistical_gates"],
        "bootstrap": report["source_specific_bootstrap"],
        "deflated_sharpe": report["deflated_sharpe"],
        "rank_stability": report["rank_stability"],
        "report_sha256": report["report_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
