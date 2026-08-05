from tradebot.v5.engines import AssetHistory, run_independent_engines
from tradebot.v5.governor import GovernorState, evaluate_governor


def test_independent_engines_fail_closed_then_emit_bounded_forecasts():
    short = run_independent_engines({"BTC": AssetHistory(tuple(float(i + 1) for i in range(20)))})
    assert short["BTC"]["trend"].reliability == 0.0

    history = AssetHistory(tuple(100.0 + i * 0.2 for i in range(240)))
    forecasts = run_independent_engines({"BTC": history})["BTC"]
    assert set(forecasts) == {"trend", "mean_reversion", "volatility_expansion"}
    for forecast in forecasts.values():
        assert 0 <= forecast.downside_probability <= 1
        assert 0 <= forecast.uncertainty <= 1
        assert 0 <= forecast.reliability <= 1


def test_governor_forces_cash_on_any_material_integrity_failure():
    decision = evaluate_governor(GovernorState(source_disagreement=True))
    assert decision.force_cash
    assert decision.exposure_multiplier == 0.0

    healthy = evaluate_governor(GovernorState(rolling_drawdown=0.02))
    assert not healthy.force_cash
    assert 0.25 <= healthy.exposure_multiplier <= 1.0
