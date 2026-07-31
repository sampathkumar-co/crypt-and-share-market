from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from tradebot.research import historical_yield_trend_v31 as v31
from tradebot.research import historical_proxy_screen_v25 as v25


class ScheduledExecutionV312Error(v31.HistoricalYieldTrendV31Error):
    """Raised when corrected scheduled execution violates its contract."""


def simulate_scheduled(
    model: v31.ModelSpec,
    bars: dict[str, dict[datetime, v25.HourlyBar]],
    features: dict[datetime, dict[str, v31.Features]],
    cash_returns: dict[datetime, float],
    start: datetime,
    end: datetime,
    cost: float,
) -> v31.SimulationResult:
    """Simulate daily risk checks with trading only on entry, exit or due rebalance."""
    current_weights: dict[str, float] = {}
    selected_assets: tuple[str, ...] = ()
    sleeve = "cash"
    age = 0
    daily_returns: list[float] = []
    cash_benchmark_returns: list[float] = []
    turnover_total = 0.0
    action_days = 0
    used_assets: set[str] = set()
    asset_contribution = {asset: 0.0 for asset in v31.ASSETS}
    crypto_contribution = 0.0
    cash_contribution = 0.0

    day = start
    while day <= end:
        signal_day = day - timedelta(days=1)
        next_day = day + timedelta(days=1)
        if signal_day not in features:
            raise ScheduledExecutionV312Error(
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
                raise ScheduledExecutionV312Error(
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
                raise ScheduledExecutionV312Error(
                    "scheduled target exposure cap violated"
                )

        turnover_by_asset = {
            asset: abs(
                target.get(asset, 0.0) - current_weights.get(asset, 0.0)
            )
            for asset in v31.ASSETS
        }
        turnover = sum(turnover_by_asset.values())
        if not trade_decision and turnover > 1e-12:
            raise ScheduledExecutionV312Error(
                "non-due trend day generated turnover"
            )
        trading_cost = 0.5 * cost * turnover
        if turnover > 1e-10:
            action_days += 1
        turnover_total += turnover
        used_assets.update(target)

        cash_return = cash_returns[day]
        cash_weight = 1.0 - target_exposure
        if cash_weight < -1e-12:
            raise ScheduledExecutionV312Error(
                "naturally drifted crypto exposure exceeded portfolio equity"
            )
        day_cash = cash_weight * cash_return
        day_crypto = 0.0
        per_asset: dict[str, float] = {}
        for asset, weight in target.items():
            raw = bars[asset][next_day].open / bars[asset][day].open - 1.0
            value = weight * raw
            per_asset[asset] = value
            day_crypto += value
        net = day_cash + day_crypto - trading_cost
        daily_returns.append(net)
        cash_benchmark_returns.append(cash_return)
        cash_contribution += day_cash
        crypto_contribution += day_crypto - trading_cost

        traded_total = sum(turnover_by_asset.values())
        for asset in v31.ASSETS:
            allocated_cost = (
                trading_cost * turnover_by_asset[asset] / traded_total
                if traded_total > 0.0
                else 0.0
            )
            asset_contribution[asset] += (
                per_asset.get(asset, 0.0) - allocated_cost
            )

        denominator = 1.0 + net
        if denominator <= 0.0:
            raise ScheduledExecutionV312Error(
                "portfolio equity became nonpositive"
            )
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
        turnover_total += final_turnover
        action_days += 1
        crypto_contribution -= final_cost
        for asset, weight in current_weights.items():
            asset_contribution[asset] -= (
                final_cost * weight / final_turnover
            )

    net_return = v31._compounded(daily_returns)
    cash_benchmark = v31._compounded(cash_benchmark_returns)
    return v31.SimulationResult(
        net_return=net_return,
        cash_benchmark_return=cash_benchmark,
        excess_return=net_return - cash_benchmark,
        maximum_drawdown=v31._maximum_drawdown(daily_returns),
        crypto_turnover=turnover_total,
        crypto_action_days=action_days,
        selected_assets=sorted(used_assets),
        daily_returns=daily_returns,
        asset_contribution={
            key: value
            for key, value in sorted(asset_contribution.items())
            if abs(value) > 1e-15
        },
        crypto_contribution=crypto_contribution,
        cash_contribution=cash_contribution,
    )


def compounded(values: list[float]) -> float:
    equity = 1.0
    for value in values:
        equity *= 1.0 + value
    return equity - 1.0


def summarize_years(
    results: dict[str, v31.SimulationResult],
) -> dict[str, Any]:
    returns = {name: item.net_return for name, item in results.items()}
    cash = {name: item.cash_benchmark_return for name, item in results.items()}
    excess = {name: item.excess_return for name, item in results.items()}
    actions = {name: item.crypto_action_days for name, item in results.items()}
    active = [name for name, count in actions.items() if count > 0]
    inactive = [name for name, count in actions.items() if count == 0]
    assets = sorted(
        {asset for item in results.values() for asset in item.selected_assets}
    )
    asset_contribution: dict[str, float] = {}
    for item in results.values():
        for asset, value in item.asset_contribution.items():
            asset_contribution[asset] = asset_contribution.get(asset, 0.0) + value
    positive_assets = [max(0.0, value) for value in asset_contribution.values()]
    positive_years = [max(0.0, value) for value in excess.values()]
    positive_asset_total = sum(positive_assets)
    positive_year_total = sum(positive_years)
    net_compounded = compounded(list(returns.values()))
    cash_compounded = compounded(list(cash.values()))
    return {
        "window_returns": returns,
        "cash_window_returns": cash,
        "excess_window_returns": excess,
        "window_action_days": actions,
        "active_years": active,
        "inactive_years": inactive,
        "active_year_count": len(active),
        "selected_assets": assets,
        "asset_net_contribution": dict(sorted(asset_contribution.items())),
        "crypto_contribution": sum(
            item.crypto_contribution for item in results.values()
        ),
        "cash_contribution": sum(
            item.cash_contribution for item in results.values()
        ),
        "crypto_turnover": sum(item.crypto_turnover for item in results.values()),
        "crypto_action_days": sum(actions.values()),
        "net_compounded_return": net_compounded,
        "cash_benchmark_compounded_return": cash_compounded,
        "excess_compounded_return": net_compounded - cash_compounded,
        "maximum_drawdown": max(
            (item.maximum_drawdown for item in results.values()),
            default=0.0,
        ),
        "maximum_positive_asset_share": (
            max(positive_assets) / positive_asset_total
            if positive_asset_total > 0.0
            else 0.0
        ),
        "maximum_positive_year_share": (
            max(positive_years) / positive_year_total
            if positive_year_total > 0.0
            else 0.0
        ),
    }


def evaluate_integrity_gates(
    standard: dict[str, Any],
    stress: dict[str, Any],
) -> dict[str, bool]:
    standard_active = set(standard["active_years"])
    stress_active = set(stress["active_years"])
    inactive = set(standard["inactive_years"])
    inactive_cash_exact = all(
        abs(
            standard["window_returns"][year]
            - standard["cash_window_returns"][year]
        )
        <= 1e-12
        and abs(
            stress["window_returns"][year]
            - stress["cash_window_returns"][year]
        )
        <= 1e-12
        and standard["window_action_days"][year] == 0
        and stress["window_action_days"][year] == 0
        for year in inactive
    )
    return {
        "all_inputs_complete": True,
        "five_positive_standard_years": all(
            value > 0.0 for value in standard["window_returns"].values()
        ),
        "five_positive_stress_years": all(
            value > 0.0 for value in stress["window_returns"].values()
        ),
        "positive_standard_excess": standard["excess_compounded_return"] > 0.0,
        "positive_stress_excess": stress["excess_compounded_return"] > 0.0,
        "four_active_years": (
            len(standard_active) >= 4
            and len(stress_active) >= 4
            and standard_active == stress_active
        ),
        "every_active_standard_year_beats_cash": all(
            standard["excess_window_returns"][year] > 0.0
            for year in standard_active
        ),
        "every_active_stress_year_beats_cash": all(
            stress["excess_window_returns"][year] > 0.0
            for year in stress_active
        ),
        "inactive_years_are_exact_cash": inactive_cash_exact,
        "twenty_actions": standard["crypto_action_days"] >= 20,
        "both_assets_selected": set(standard["selected_assets"]) == set(v31.ASSETS),
        "drawdown_cap": (
            standard["maximum_drawdown"] <= 0.05
            and stress["maximum_drawdown"] <= 0.05
        ),
        "asset_concentration": (
            standard["maximum_positive_asset_share"] <= 0.80
            and stress["maximum_positive_asset_share"] <= 0.80
        ),
        "year_concentration": (
            standard["maximum_positive_year_share"] <= 0.80
            and stress["maximum_positive_year_share"] <= 0.80
        ),
    }
