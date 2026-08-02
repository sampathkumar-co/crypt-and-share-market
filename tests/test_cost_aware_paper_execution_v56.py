from __future__ import annotations

import pytest

from tradebot.research import cost_aware_paper_execution_v56 as v56


def signal(**overrides: float | str) -> v56.MarketSignal:
    values: dict[str, float | str] = {
        "asset": "BTC",
        "score": 1.0,
        "ret5": 0.004,
        "ret15": 0.012,
        "ret60": 0.020,
        "last_close": 101.0,
        "vwap20": 100.0,
        "volatility60": 0.0002,
        "spread_bps": 0.5,
    }
    values.update(overrides)
    return v56.MarketSignal(**values)  # type: ignore[arg-type]


def bid_for_net(entry: float, net_bps: float, policy: v56.CostPolicy) -> float:
    return (
        entry
        * (1.0 + policy.one_way_fee)
        * (1.0 + net_bps / v56.BPS)
        / (1.0 - policy.one_way_fee)
    )


def test_exact_fee_break_even_is_more_than_twenty_bps() -> None:
    assert v56.fee_break_even_bps(0.001) == pytest.approx(20.02002002)


def test_required_edge_includes_every_frozen_cost() -> None:
    policy = v56.CostPolicy()
    required = v56.required_edge_bps(1.36, policy)
    assert required == pytest.approx(36.38002002)


def test_august_two_sol_entry_is_rejected() -> None:
    sol = signal(
        asset="SOL",
        score=0.8222827642993094,
        ret5=0.001772807854902414,
        ret15=0.0032777929527449956,
        ret60=0.004649890590809447,
        last_close=73.46,
        vwap20=73.43293758947411,
        volatility60=0.00035268048736489184,
        spread_bps=1.3616557734219548,
    )
    assessment = v56.assess_entry(sol, v56.CostPolicy())
    assert assessment.eligible is False
    assert assessment.lower_bound_edge_bps < assessment.required_edge_bps
    assert "does not cover costs" in assessment.reason


def test_strong_signal_can_clear_cost_aware_gate() -> None:
    decision = v56.choose_entry([signal(asset="ETH")])
    assert decision.selected_asset == "ETH"
    assert decision.allocation == pytest.approx(0.25)
    assert decision.assessments[0].margin_bps > 0.0


def test_zero_loss_mode_is_cash_even_for_strong_signal() -> None:
    decision = v56.choose_entry(
        [signal()],
        mode=v56.ExecutionMode.GUARANTEED_CASH,
    )
    assert decision.selected_asset is None
    assert decision.allocation == 0.0
    assert "no market exposure" in decision.reason


def test_weak_universe_remains_cash() -> None:
    decision = v56.choose_entry([
        signal(asset="BTC", ret5=-0.001),
        signal(asset="ETH", spread_bps=8.0),
    ])
    assert decision.selected_asset is None
    assert decision.allocation == 0.0


def test_break_even_bid_has_zero_net_return() -> None:
    policy = v56.CostPolicy()
    entry = 73.45
    bid = v56.break_even_exit_bid(entry, policy)
    assert v56.net_return(entry, bid, policy) == pytest.approx(0.0)


def test_take_profit_exit() -> None:
    policy = v56.CostPolicy()
    entry = 100.0
    current = bid_for_net(entry, 30.0, policy)
    decision = v56.evaluate_exit(
        v56.PositionState("BTC", entry, current),
        current,
        600,
        policy,
    )
    assert decision.action == "SELL_TAKE_PROFIT"
    assert decision.current_net_bps == pytest.approx(30.0)


def test_profit_protection_exit() -> None:
    policy = v56.CostPolicy()
    entry = 100.0
    peak = bid_for_net(entry, 20.0, policy)
    current = bid_for_net(entry, 4.0, policy)
    decision = v56.evaluate_exit(
        v56.PositionState("BTC", entry, peak),
        current,
        900,
        policy,
    )
    assert decision.action == "SELL_PROFIT_PROTECTION"
    assert decision.peak_net_bps == pytest.approx(20.0)


def test_hard_stop_exit() -> None:
    policy = v56.CostPolicy()
    entry = 100.0
    current = bid_for_net(entry, -30.0, policy)
    decision = v56.evaluate_exit(
        v56.PositionState("BTC", entry, entry),
        current,
        120,
        policy,
    )
    assert decision.action == "SELL_HARD_STOP"


def test_profitable_momentum_reversal_exit() -> None:
    policy = v56.CostPolicy()
    entry = 100.0
    current = bid_for_net(entry, 8.0, policy)
    decision = v56.evaluate_exit(
        v56.PositionState("BTC", entry, current),
        current,
        500,
        policy,
        momentum_reversal=True,
    )
    assert decision.action == "SELL_MOMENTUM_REVERSAL"


def test_time_exit_can_record_a_loss() -> None:
    policy = v56.CostPolicy()
    entry = 100.0
    current = bid_for_net(entry, -10.0, policy)
    decision = v56.evaluate_exit(
        v56.PositionState("BTC", entry, entry),
        current,
        policy.max_hold_seconds,
        policy,
    )
    assert decision.action == "SELL_TIME_EXIT"
    assert decision.current_net_bps == pytest.approx(-10.0)


def test_hold_when_no_exit_rule_is_reached() -> None:
    policy = v56.CostPolicy()
    entry = 100.0
    current = bid_for_net(entry, 2.0, policy)
    decision = v56.evaluate_exit(
        v56.PositionState("BTC", entry, current),
        current,
        300,
        policy,
    )
    assert decision.action == "HOLD"


def test_runner_applies_slippage_on_both_sides() -> None:
    from tradebot.research import cost_aware_paper_hour_v56 as runner

    policy = v56.CostPolicy(slippage_bps_per_side=2.5)
    assert runner.effective_entry(100.0, policy) == pytest.approx(100.025)
    assert runner.effective_exit(100.0, policy) == pytest.approx(99.975)


def test_runner_hash_is_deterministic() -> None:
    from tradebot.research import cost_aware_paper_hour_v56 as runner

    first = runner.canonical_hash({"b": 2, "a": 1})
    second = runner.canonical_hash({"a": 1, "b": 2})
    assert first == second


def test_runner_cli_defaults_to_loss_averse_mode() -> None:
    from tradebot.research import cost_aware_paper_hour_v56 as runner

    args = runner.parse_args([])
    assert args.mode == v56.ExecutionMode.LOSS_AVERSE_PAPER.value
    assert args.duration == 3600
