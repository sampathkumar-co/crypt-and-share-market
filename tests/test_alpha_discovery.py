from __future__ import annotations

import pytest

from tradebot.backtest.alpha_discovery import (
    TRACK_A_FAMILIES,
    AlphaDiscoveryConfig,
    AlphaDiscoveryPeriod,
    _summarize_family,
)


def period(symbol: str, index: int, net_return: float, baseline_return: float = 0.002) -> AlphaDiscoveryPeriod:
    metrics = {
        "net_return": net_return,
        "gross_return": net_return,
        "cash_return": 0.0,
        "buy_and_hold_return": 0.01,
        "excess_return": net_return - 0.01,
        "max_drawdown": 0.05,
        "win_rate": 0.60,
        "trades": 2,
        "trades_per_100_bars": 3.0,
        "average_holding_bars": 8.0,
        "turnover": 0.4,
        "cost_drag_ratio": 0.20,
        "total_fees": 10.0,
        "total_tax": 5.0,
        "ending_cash": 100000.0 * (1.0 + net_return),
        "sharpe_ratio": 0.5,
        "profit_factor": 1.2,
    }
    baseline = {**metrics, "net_return": baseline_return}
    return AlphaDiscoveryPeriod(
        symbol=symbol,
        family="multi_timeframe_trend",
        period=index,
        train_start="2025-01-01T00:00:00",
        train_end="2025-06-29T00:00:00",
        unseen_start="2025-06-30T00:00:00",
        unseen_end="2025-08-28T00:00:00",
        abstained=False,
        selection_reasons=[],
        selected_parameters={"fast_window": 5},
        selected_execution={"min_holding_bars": 5},
        training_stability={"stable": True},
        metrics=metrics,
        cash_return=0.0,
        buy_and_hold_return=0.01,
        existing_strategy_metrics={"momentum": baseline},
        v05_reference_strategy="momentum",
        v05_reference_metrics=baseline,
        improvement_over_v05_reference=net_return - baseline_return,
    )


def test_track_a_contains_no_more_than_three_distinct_families():
    assert TRACK_A_FAMILIES == (
        "multi_timeframe_trend",
        "compression_breakout_retest",
        "cross_asset_relative_strength",
    )
    assert len(TRACK_A_FAMILIES) == 3


def test_track_a_window_sizes_are_frozen():
    with pytest.raises(ValueError, match="fixed 180-bar training"):
        AlphaDiscoveryConfig(train_size=120)
    with pytest.raises(ValueError, match="fixed 180-bar training"):
        AlphaDiscoveryConfig(test_size=30)


def test_promising_summary_requires_positive_multi_asset_unseen_results():
    periods = [
        period("BTCUSDT", 1, 0.020),
        period("BTCUSDT", 2, 0.015),
        period("BTCUSDT", 3, 0.010),
        period("ETHUSDT", 1, 0.018),
        period("ETHUSDT", 2, 0.012),
        period("ETHUSDT", 3, 0.008),
    ]

    summary = _summarize_family(
        "multi_timeframe_trend",
        periods,
        AlphaDiscoveryConfig(),
    )

    assert summary.promising
    assert summary.deployed_periods == 6
    assert summary.positive_deployed_fraction == 1.0
    assert summary.profitable_assets == ["BTCUSDT", "ETHUSDT"]
    assert summary.average_improvement_over_v05 > 0


def test_track_a_rejects_profit_dependent_on_one_asset():
    periods = [
        period("BTCUSDT", 1, 0.030, baseline_return=-0.005),
        period("BTCUSDT", 2, 0.030, baseline_return=-0.005),
        period("BTCUSDT", 3, 0.030, baseline_return=-0.005),
        period("ETHUSDT", 1, -0.005, baseline_return=-0.010),
        period("ETHUSDT", 2, -0.005, baseline_return=-0.010),
        period("ETHUSDT", 3, -0.005, baseline_return=-0.010),
    ]

    summary = _summarize_family(
        "multi_timeframe_trend",
        periods,
        AlphaDiscoveryConfig(),
    )

    assert not summary.promising
    assert "positive_results_dependent_on_fewer_than_two_assets" in summary.reasons
