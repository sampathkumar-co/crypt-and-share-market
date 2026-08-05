from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
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
from tradebot.research import parameter_neighborhood_ensemble_v61 as evaluation
from tradebot.research import parameter_neighborhood_ensemble_v611 as natural_drift


SCHEMA_VERSION = "6.3-dual-source-consensus"
PROTOCOL_PATH = Path("research/V63_DUAL_SOURCE_CONSENSUS_PROTOCOL.md")
TRIAL_COUNT = 227
FROZEN_GRID_SHARPE_STD = 0.17603369374678823
EXPECTED_V61_SHA256 = "b6f5e75957cf31f26d7ebe2d1f341d67901dd4dfb3ad3d3f6b10a4be3fe34692"
EXPECTED_V62_SHA256 = "e56cdaa5b859da435a32f68f12691a15dd45a23792671e01831d122ea96c97a5"
MEMBERS = evaluation.MEMBERS
v31 = evaluation.v31


class DualSourceConsensusV63Error(RuntimeError):
    """Raised when the frozen dual-source mechanism cannot be reproduced."""


@dataclass(frozen=True)
class SourceTargetDay:
    day: datetime
    target: Mapping[str, float]
    genuine_decision: bool


@dataclass(frozen=True)
class ExecutionSimulation:
    net_return: float
    cash_return: float
    excess_return: float
    maximum_drawdown: float
    action_days: int
    daily_returns: tuple[float, ...]
    cash_daily_returns: tuple[float, ...]
    interval_excess_contributions: tuple[float, ...]
    maximum_positive_interval_share: float


def _validate_dependency(
    report: Mapping[str, Any], *, expected_sha: str, expected_status: str, name: str
) -> str:
    payload = dict(report)
    claimed = str(payload.pop("report_sha256", ""))
    computed = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    if claimed != computed:
        raise DualSourceConsensusV63Error(f"{name} report hash does not match contents")
    if claimed != expected_sha:
        raise DualSourceConsensusV63Error(
            f"{name} report does not reproduce frozen SHA: {claimed} != {expected_sha}"
        )
    if report.get("paper_only") is not True or report.get("authorizes_trading") is not False:
        raise DualSourceConsensusV63Error(f"{name} safety boundary changed")
    if report.get("status") != expected_status:
        raise DualSourceConsensusV63Error(f"{name} status changed")
    return claimed


def _mean_target(member_targets: Mapping[str, Mapping[str, float]]) -> dict[str, float]:
    expected = {model.model_id for model in MEMBERS}
    if set(member_targets) != expected:
        raise DualSourceConsensusV63Error("mean target requires all 16 frozen members")
    result: dict[str, float] = {}
    for asset in v31.ASSETS:
        weight = sum(
            float(member_targets[model.model_id].get(asset, 0.0))
            for model in MEMBERS
        ) / len(MEMBERS)
        if weight > 1e-15:
            result[asset] = weight
    return result


def _dual_target(
    binance_target: Mapping[str, float],
    coinbase_target: Mapping[str, float],
) -> dict[str, float]:
    result = {
        asset: min(
            float(binance_target.get(asset, 0.0)),
            float(coinbase_target.get(asset, 0.0)),
        )
        for asset in v31.ASSETS
    }
    if any(weight < -1e-15 for weight in result.values()):
        raise DualSourceConsensusV63Error("dual-source target received negative weight")
    return {asset: weight for asset, weight in result.items() if weight > 1e-15}


def build_source_target_path(
    bars: Mapping[str, Mapping[datetime, Any]],
    features: Mapping[datetime, Mapping[str, v31.Features]],
    cash_returns: Mapping[datetime, float],
    start: datetime,
    end: datetime,
    cost: float,
    *,
    signal_lag_days: int,
) -> dict[datetime, SourceTargetDay]:
    if len(MEMBERS) != 16:
        raise DualSourceConsensusV63Error("frozen source engine must contain 16 members")
    if signal_lag_days < 1:
        raise DualSourceConsensusV63Error("signal lag must be at least one day")

    states = {model.model_id: evaluation.MemberState() for model in MEMBERS}
    source_weights: dict[str, float] = {}
    path: dict[datetime, SourceTargetDay] = {}
    day = start
    while day <= end:
        signal_day = day - timedelta(days=signal_lag_days)
        next_day = day + timedelta(days=1)
        if signal_day not in features:
            raise DualSourceConsensusV63Error(
                f"features unavailable for {v31._utc(signal_day)}"
            )
        cash_return = float(cash_returns[day])
        member_targets: dict[str, dict[str, float]] = {}
        pending_states: dict[str, evaluation.MemberState] = {}
        any_member_decision = False

        for model in MEMBERS:
            state = states[model.model_id]
            target, selected, sleeve, age, trade_decision = evaluation._member_target(
                model, state, features[signal_day]
            )
            exposure = sum(target.values())
            if trade_decision and exposure > model.maximum_exposure + 1e-12:
                raise DualSourceConsensusV63Error(
                    f"new member target cap violated by {model.model_id}"
                )
            if exposure < -1e-12 or exposure >= 1.0:
                raise DualSourceConsensusV63Error(
                    f"invalid member exposure for {model.model_id}"
                )
            turnover = sum(
                abs(target.get(asset, 0.0) - state.current_weights.get(asset, 0.0))
                for asset in v31.ASSETS
            )
            if not trade_decision and turnover > 1e-12:
                raise DualSourceConsensusV63Error(
                    f"non-due turnover generated by {model.model_id}"
                )
            any_member_decision = any_member_decision or trade_decision
            member_cost = 0.5 * cost * turnover
            pending_states[model.model_id] = evaluation.MemberState(
                current_weights=evaluation._drift_weights(
                    target, bars, day, next_day, cash_return, member_cost
                ),
                selected_assets=selected,
                sleeve=sleeve,
                age=age,
            )
            member_targets[model.model_id] = target

        target = _mean_target(member_targets) if any_member_decision else dict(source_weights)
        exposure = sum(target.values())
        if exposure < -1e-12 or exposure >= 1.0:
            raise DualSourceConsensusV63Error("invalid source ensemble exposure")
        turnover = sum(
            abs(target.get(asset, 0.0) - source_weights.get(asset, 0.0))
            for asset in v31.ASSETS
        )
        if not any_member_decision and turnover > 1e-12:
            raise DualSourceConsensusV63Error("source engine generated hidden turnover")
        source_cost = 0.5 * cost * turnover
        path[day] = SourceTargetDay(
            day=day,
            target=dict(target),
            genuine_decision=any_member_decision,
        )
        source_weights = evaluation._drift_weights(
            target, bars, day, next_day, cash_return, source_cost
        )
        states = pending_states
        day += timedelta(days=1)
    return path


def simulate_execution(
    execution_bars: Mapping[str, Mapping[datetime, Any]],
    binance_path: Mapping[datetime, SourceTargetDay],
    coinbase_path: Mapping[datetime, SourceTargetDay],
    cash_returns: Mapping[datetime, float],
    start: datetime,
    end: datetime,
    cost: float,
) -> ExecutionSimulation:
    if set(binance_path) != set(coinbase_path):
        raise DualSourceConsensusV63Error("source target paths do not align")
    weights: dict[str, float] = {}
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
                (interval_strategy_equity - 1.0) - (interval_cash_equity - 1.0)
            )
        interval_active = False
        interval_strategy_equity = 1.0
        interval_cash_equity = 1.0

    day = start
    while day <= end:
        next_day = day + timedelta(days=1)
        left = binance_path[day]
        right = coinbase_path[day]
        genuine_decision = left.genuine_decision or right.genuine_decision
        target = _dual_target(left.target, right.target) if genuine_decision else dict(weights)
        exposure = sum(target.values())
        if exposure < -1e-12 or exposure >= 1.0:
            raise DualSourceConsensusV63Error("invalid dual execution exposure")
        turnover = sum(
            abs(target.get(asset, 0.0) - weights.get(asset, 0.0))
            for asset in v31.ASSETS
        )
        if not genuine_decision and turnover > 1e-12:
            raise DualSourceConsensusV63Error("dual portfolio generated hidden turnover")
        trading_cost = 0.5 * cost * turnover
        if turnover > 1e-10:
            close_interval()
            interval_active = True
            action_days += 1
        cash_return = float(cash_returns[day])
        crypto_return = sum(
            weight
            * (execution_bars[asset][next_day].open / execution_bars[asset][day].open - 1.0)
            for asset, weight in target.items()
        )
        net = (1.0 - exposure) * cash_return + crypto_return - trading_cost
        daily_returns.append(net)
        cash_daily.append(cash_return)
        if interval_active:
            interval_strategy_equity *= 1.0 + net
            interval_cash_equity *= 1.0 + cash_return
        weights = evaluation._drift_weights(
            target, execution_bars, day, next_day, cash_return, trading_cost
        )
        day += timedelta(days=1)

    final_turnover = sum(weights.values())
    if final_turnover > 1e-12:
        final_cost = 0.5 * cost * final_turnover
        daily_returns.append(-final_cost)
        cash_daily.append(0.0)
        action_days += 1
        if not interval_active:
            interval_active = True
        interval_strategy_equity *= 1.0 - final_cost
    close_interval()

    net_return = robustness_base._compounded(daily_returns)
    cash_return = robustness_base._compounded(cash_daily)
    return ExecutionSimulation(
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


def _mode_periods(
    binance_bars: Mapping[str, Mapping[datetime, Any]],
    binance_features: Mapping[datetime, Mapping[str, v31.Features]],
    coinbase_bars: Mapping[str, Mapping[datetime, Any]],
    coinbase_features: Mapping[datetime, Mapping[str, v31.Features]],
    cash_returns: Mapping[datetime, float],
    *,
    cost: float,
    signal_lag_days: int,
) -> tuple[dict[str, ExecutionSimulation], dict[str, ExecutionSimulation]]:
    binance_execution: dict[str, ExecutionSimulation] = {}
    coinbase_execution: dict[str, ExecutionSimulation] = {}
    for period in v31.VERIFICATION_PERIODS:
        left = build_source_target_path(
            binance_bars,
            binance_features,
            cash_returns,
            period.start,
            period.end,
            cost,
            signal_lag_days=signal_lag_days,
        )
        right = build_source_target_path(
            coinbase_bars,
            coinbase_features,
            cash_returns,
            period.start,
            period.end,
            cost,
            signal_lag_days=signal_lag_days,
        )
        binance_execution[period.name] = simulate_execution(
            binance_bars, left, right, cash_returns, period.start, period.end, cost
        )
        coinbase_execution[period.name] = simulate_execution(
            coinbase_bars, left, right, cash_returns, period.start, period.end, cost
        )
    return binance_execution, coinbase_execution


def _combine(results: Mapping[str, ExecutionSimulation]) -> dict[str, Any]:
    ordered = [results[period.name] for period in v31.VERIFICATION_PERIODS]
    net_returns = [item.net_return for item in ordered]
    cash_returns = [item.cash_return for item in ordered]
    excess_returns = [item.excess_return for item in ordered]
    daily = [value for item in ordered for value in item.daily_returns]
    cash_daily = [value for item in ordered for value in item.cash_daily_returns]
    intervals = [
        value for item in ordered for value in item.interval_excess_contributions
    ]
    positive_years = [max(0.0, value) for value in excess_returns]
    total_positive = sum(positive_years)
    return {
        "net_compounded_return": robustness_base._compounded(net_returns),
        "cash_compounded_return": robustness_base._compounded(cash_returns),
        "excess_compounded_return": robustness_base._compounded(net_returns)
        - robustness_base._compounded(cash_returns),
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
        "maximum_positive_year_share": 0.0
        if total_positive <= 0.0
        else max(positive_years) / total_positive,
        "maximum_positive_interval_share": robustness_base._positive_concentration(
            intervals
        ),
        "relative_daily_returns": [
            (1.0 + strategy) / (1.0 + cash) - 1.0
            for strategy, cash in zip(daily, cash_daily, strict=True)
        ],
        "decision_interval_count": len(intervals),
        "positive_decision_interval_count": sum(value > 0.0 for value in intervals),
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
    standard_windows = {
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
        "standard_windows": standard_windows,
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


def _discovery_candidate_series(
    binance_bars: Mapping[str, Mapping[datetime, Any]],
    binance_features: Mapping[datetime, Mapping[str, v31.Features]],
    coinbase_bars: Mapping[str, Mapping[datetime, Any]],
    coinbase_features: Mapping[datetime, Mapping[str, v31.Features]],
    cash_returns: Mapping[datetime, float],
) -> np.ndarray:
    relative: list[float] = []
    for period in v31.DISCOVERY_PERIODS:
        left = build_source_target_path(
            binance_bars,
            binance_features,
            cash_returns,
            period.start,
            period.end,
            v31.STRESS_COST,
            signal_lag_days=1,
        )
        right = build_source_target_path(
            coinbase_bars,
            coinbase_features,
            cash_returns,
            period.start,
            period.end,
            v31.STRESS_COST,
            signal_lag_days=1,
        )
        result = simulate_execution(
            binance_bars,
            left,
            right,
            cash_returns,
            period.start,
            period.end,
            v31.STRESS_COST,
        )
        relative.extend(
            (1.0 + strategy) / (1.0 + cash) - 1.0
            for strategy, cash in zip(
                result.daily_returns, result.cash_daily_returns, strict=True
            )
        )
    return np.asarray(relative, dtype=float)


def _rank_stability(
    binance_bars: Mapping[str, Mapping[datetime, Any]],
    binance_features: Mapping[datetime, Mapping[str, v31.Features]],
    coinbase_bars: Mapping[str, Mapping[datetime, Any]],
    coinbase_features: Mapping[datetime, Mapping[str, v31.Features]],
    cash_returns: Mapping[datetime, float],
) -> dict[str, Any]:
    member_series = {
        model.model_id: v603._relative_series_for_model(
            model, binance_bars, binance_features, cash_returns
        )
        for model in MEMBERS
    }
    candidate = _discovery_candidate_series(
        binance_bars,
        binance_features,
        coinbase_bars,
        coinbase_features,
        cash_returns,
    )
    series = dict(member_series)
    series["dual-source-consensus"] = candidate
    lengths = {len(values) for values in series.values()}
    if len(lengths) != 1:
        raise DualSourceConsensusV63Error("rank-stability series do not align")
    observations = lengths.pop()
    base, remainder = divmod(observations, 8)
    blocks: list[np.ndarray] = []
    start = 0
    for index in range(8):
        stop = start + base + (1 if index < remainder else 0)
        blocks.append(np.arange(start, stop, dtype=int))
        start = stop
    percentiles: list[float] = []
    for chosen in itertools.combinations(range(1, 8), 3):
        out_blocks = tuple(index for index in range(8) if index not in (0, *chosen))
        out_indices = np.concatenate([blocks[index] for index in out_blocks])
        scores = {
            name: v603.annualized_sharpe(values[out_indices])
            for name, values in series.items()
        }
        ordered = sorted(scores, key=lambda name: (scores[name], name))
        rank = ordered.index("dual-source-consensus") + 1
        percentiles.append((rank - 0.5) / len(ordered))
    top_half_fraction = sum(value >= 0.5 for value in percentiles) / len(percentiles)
    median_percentile = float(median(percentiles))
    return {
        "members": 16,
        "candidates_including_dual_source": 17,
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
    v61_report: Mapping[str, Any],
    v62_report: Mapping[str, Any],
) -> dict[str, Any]:
    tournament._validate_report_sha(
        v312_report, tournament.EXPECTED_V312_SHA256, "v3.1.2"
    )
    tournament._validate_report_sha(v32_report, tournament.EXPECTED_V32_SHA256, "v3.2")
    v603_digest = tournament._validate_self_hashed_report(
        v603_report, "v6.0.3 chronology audit"
    )
    v61_digest = _validate_dependency(
        v61_report,
        expected_sha=EXPECTED_V61_SHA256,
        expected_status="ENSEMBLE_REJECTED",
        name="v6.1",
    )
    v62_digest = _validate_dependency(
        v62_report,
        expected_sha=EXPECTED_V62_SHA256,
        expected_status="CONSENSUS_ENSEMBLE_REJECTED",
        name="v6.2",
    )
    if not PROTOCOL_PATH.is_file():
        raise DualSourceConsensusV63Error("v6.3 protocol is missing")

    binance_bars, binance_features, binance_cash = robustness_base._load_binance()
    coinbase_bars, coinbase_features, coinbase_cash = (
        robustness_warmup._load_coinbase_with_real_warmup()
    )
    for period in (*v31.DISCOVERY_PERIODS, *v31.VERIFICATION_PERIODS):
        day = period.start
        while day <= period.end:
            if abs(float(binance_cash[day]) - float(coinbase_cash[day])) > 1e-15:
                raise DualSourceConsensusV63Error("cash-return histories do not align")
            day += timedelta(days=1)
    cash_returns = binance_cash

    standard_binance, standard_coinbase = _mode_periods(
        binance_bars,
        binance_features,
        coinbase_bars,
        coinbase_features,
        cash_returns,
        cost=v31.STANDARD_COST,
        signal_lag_days=1,
    )
    stress_binance, stress_coinbase = _mode_periods(
        binance_bars,
        binance_features,
        coinbase_bars,
        coinbase_features,
        cash_returns,
        cost=v31.STRESS_COST,
        signal_lag_days=1,
    )
    delayed_binance, delayed_coinbase = _mode_periods(
        binance_bars,
        binance_features,
        coinbase_bars,
        coinbase_features,
        cash_returns,
        cost=v31.STANDARD_COST,
        signal_lag_days=2,
    )
    sources = {
        "binance_execution": {
            "standard": _combine(standard_binance),
            "stress": _combine(stress_binance),
            "delayed": _combine(delayed_binance),
        },
        "coinbase_execution": {
            "standard": _combine(standard_coinbase),
            "stress": _combine(stress_coinbase),
            "delayed": _combine(delayed_coinbase),
        },
    }
    conservative = _conservative(
        sources["binance_execution"], sources["coinbase_execution"]
    )

    protocol_digest = hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()
    master_seed = int.from_bytes(
        hashlib.sha256(
            "|".join(
                [
                    tournament.EXPECTED_V312_SHA256,
                    tournament.EXPECTED_V32_SHA256,
                    v603_digest,
                    v61_digest,
                    v62_digest,
                    protocol_digest,
                ]
            ).encode("utf-8")
        ).digest()[:8],
        "big",
    )
    source_bootstrap: dict[str, list[dict[str, Any]]] = {}
    source_series: dict[str, np.ndarray] = {}
    for source_index, (source, payload) in enumerate(sources.items()):
        values = np.asarray(payload["standard"]["relative_daily_returns"], dtype=float)
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
        source_series["binance_execution"],
        source_series["coinbase_execution"],
        trial_count=TRIAL_COUNT,
        sharpe_trial_std=FROZEN_GRID_SHARPE_STD,
    )
    stability = _rank_stability(
        binance_bars,
        binance_features,
        coinbase_bars,
        coinbase_features,
        cash_returns,
    )

    material_gates = {
        "annualized_return_at_least_5pct": conservative["annualized_standard_return"] >= 0.05,
        "annualized_excess_over_cash_at_least_2pct": conservative[
            "annualized_excess_over_cash"
        ]
        >= 0.02,
        "positive_stress_return": conservative["stress_return"] > 0.0,
        "four_of_five_positive_standard_years": sum(
            value > 0.0 for value in conservative["standard_windows"].values()
        )
        >= 4,
        "three_of_five_positive_stress_years": sum(
            value > 0.0 for value in conservative["stress_windows"].values()
        )
        >= 3,
        "at_least_30_actions": conservative["actions"] >= 30,
        "drawdown_at_most_5pct": conservative["maximum_drawdown"] <= 0.05,
        "positive_delayed_excess": conservative["delayed_excess_over_cash"] > 0.0,
        "interval_concentration_at_most_20pct": conservative[
            "maximum_positive_interval_share"
        ]
        <= 0.20,
        "year_concentration_at_most_50pct": conservative[
            "maximum_positive_year_share"
        ]
        <= 0.50,
        "both_signal_sources_complete": True,
        "both_execution_sources_complete": True,
    }
    statistical_gates = {
        "both_execution_sources_bootstrap_positive": bootstrap_passed,
        "direct_lineage_dsr": dsr.passed,
        "rank_stability": bool(stability["passed"]),
    }
    passed = all(material_gates.values()) and all(statistical_gates.values())
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_continuous_paper": False,
        "retrospective_dates_exposed": True,
        "untouched_historical_dates": False,
        "execution_replication_is_not_independent_signal_replication": True,
        "protocol_sha256": protocol_digest,
        "dependencies": {
            "v603": v603_digest,
            "v61": v61_digest,
            "v62": v62_digest,
        },
        "sources": sources,
        "conservative": conservative,
        "source_specific_bootstrap": source_bootstrap,
        "deflated_sharpe": asdict(dsr),
        "rank_stability": stability,
        "material_gates": material_gates,
        "statistical_gates": statistical_gates,
        "status": "RETROSPECTIVE_DUAL_SOURCE_CANDIDATE_FORWARD_REQUIRED"
        if passed
        else "DUAL_SOURCE_CONSENSUS_REJECTED",
        "historical_breakthrough": False,
        "forward_breakthrough": False,
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run v6.3 dual-source consensus")
    parser.add_argument("--v312-json", type=Path, required=True)
    parser.add_argument("--v32-json", type=Path, required=True)
    parser.add_argument("--v603-json", type=Path, required=True)
    parser.add_argument("--v61-json", type=Path, required=True)
    parser.add_argument("--v62-json", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_report(
        json.loads(args.v312_json.read_text(encoding="utf-8")),
        json.loads(args.v32_json.read_text(encoding="utf-8")),
        json.loads(args.v603_json.read_text(encoding="utf-8")),
        json.loads(args.v61_json.read_text(encoding="utf-8")),
        json.loads(args.v62_json.read_text(encoding="utf-8")),
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "conservative": report["conservative"],
                "material_gates": report["material_gates"],
                "statistical_gates": report["statistical_gates"],
                "bootstrap": report["source_specific_bootstrap"],
                "deflated_sharpe": report["deflated_sharpe"],
                "rank_stability": report["rank_stability"],
                "report_sha256": report["report_sha256"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
