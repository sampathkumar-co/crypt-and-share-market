from __future__ import annotations

import pytest

from tradebot.research import intraday_signal_engine_v70 as engine
from tradebot.research.intraday_alpha_lab_v70 import CandidateFamily


def bars(start: float, step: float, *, breakout: bool = False) -> list[engine.HourBar]:
    values: list[engine.HourBar] = []
    price = start
    for index in range(30):
        price *= 1.0 + step
        high = price * 1.001
        volume = 100.0
        if breakout and index == 29:
            price *= 1.02
            high = price
            volume = 200.0
        values.append(engine.HourBar(price, high, price * 0.999, volume))
    return values


def history() -> dict[str, list[engine.HourBar]]:
    return {"BTC": bars(50_000.0, 0.003), "ETH": bars(2_000.0, 0.0002)}


def test_tournament_contains_exact_frozen_families() -> None:
    decisions = engine.run_first_tournament(history())
    assert {decision.family for decision in decisions} == set(CandidateFamily)
    assert all(decision.paper_only for decision in decisions)
    assert not any(decision.authorizes_trading for decision in decisions)


def test_trend_and_relative_strength_choose_btc_with_capped_target() -> None:
    data = history()
    trend = engine.hourly_trend(data)
    relative = engine.relative_strength(data)
    assert trend.target == {"BTC": 0.05}
    assert relative.target == {"BTC": 0.05}


def test_absolute_trend_veto_forces_cash() -> None:
    data = {"BTC": bars(50_000.0, -0.001), "ETH": bars(2_000.0, -0.002)}
    decision = engine.relative_strength(data)
    assert decision.target == {}
    assert decision.reason == "absolute-trend veto"


def test_breakout_requires_volume_confirmation() -> None:
    data = history()
    assert engine.volatility_breakout(data).target == {}
    data["ETH"] = bars(2_000.0, 0.0, breakout=True)
    decision = engine.volatility_breakout(data)
    assert decision.target == {"ETH": 0.05}


def test_missing_or_invalid_data_fails_closed() -> None:
    with pytest.raises(ValueError, match="BTC and ETH"):
        engine.hourly_trend({"BTC": bars(50_000.0, 0.001)})
    bad = history()
    bad["ETH"][-1] = engine.HourBar(0.0, 1.0, 0.0, 1.0)
    with pytest.raises(ValueError, match="invalid"):
        engine.run_first_tournament(bad)
