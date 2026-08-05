from __future__ import annotations

import pytest

from tradebot.research.intraday_alpha_lab_v70 import CandidateFamily
from tradebot.research.intraday_execution_v70 import ExecutionPrices, evaluate_paper_episode
from tradebot.research.intraday_signal_engine_v70 import SignalDecision


def decision(target: dict[str, float] | None = None) -> SignalDecision:
    return SignalDecision(
        family=CandidateFamily.HOURLY_TREND,
        target=target or {"BTC": 0.05},
        lower_bound_edge_bps=75.0,
        reason="test",
    )


def prices(**overrides: object) -> ExecutionPrices:
    values: dict[str, object] = {
        "next_five_minute_open": {"BTC": 100.0, "ETH": 50.0},
        "following_hour_open": {"BTC": 102.0, "ETH": 50.0},
        "delayed_five_minute_open": {"BTC": 101.0, "ETH": 50.0},
        "primary_source": "Binance",
        "replication_source": "Coinbase",
        "sources_consistent": True,
        "next_bar_after_signal": True,
    }
    values.update(overrides)
    return ExecutionPrices(**values)  # type: ignore[arg-type]


def test_next_bar_episode_applies_frozen_costs() -> None:
    result = evaluate_paper_episode(decision(), prices())
    assert result.action_count == 1
    assert result.standard_net_return == pytest.approx(0.0009)
    assert result.stress_net_return == pytest.approx(0.0008)
    assert result.delayed_stress_net_return == pytest.approx(0.0002450495)
    assert result.paper_only is True
    assert result.authorizes_trading is False


def test_conflicting_sources_force_cash() -> None:
    result = evaluate_paper_episode(decision(), prices(sources_consistent=False))
    assert result.target == {}
    assert result.action_count == 0
    assert result.stress_net_return == 0.0


def test_execution_clock_violation_forces_cash() -> None:
    result = evaluate_paper_episode(decision(), prices(next_bar_after_signal=False))
    assert result.target == {}
    assert "clock" in result.reason


def test_missing_price_forces_cash() -> None:
    result = evaluate_paper_episode(
        decision(),
        prices(next_five_minute_open={"BTC": 100.0}),
    )
    assert result.target == {}
    assert result.action_count == 0


def test_non_authorizing_invariant_is_enforced() -> None:
    unsafe = SignalDecision(
        family=CandidateFamily.HOURLY_TREND,
        target={"BTC": 0.05},
        lower_bound_edge_bps=75.0,
        reason="unsafe test",
        authorizes_trading=True,
    )
    with pytest.raises(ValueError, match="non-authorizing"):
        evaluate_paper_episode(unsafe, prices())
