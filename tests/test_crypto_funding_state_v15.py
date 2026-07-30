from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tradebot.backtest import crypto_funding_state_v15 as v15
from tradebot.backtest import crypto_multiregime_4h as base
from tradebot.models import Candle


def funding_series(
    start: datetime,
    days: int,
    spacing_hours: int,
    value: float,
) -> dict[datetime, float]:
    count = days * 24 // spacing_hours + 1
    return {
        start + timedelta(hours=spacing_hours * index): value
        for index in range(count)
    }


def recovery_candles(start: datetime, final_close: float = 85.0) -> list[Candle]:
    closes = [100.0] * 176 + [80.0, 81.0, 82.0, final_close]
    output = []
    for index, close in enumerate(closes):
        output.append(
            Candle(
                timestamp=start + timedelta(hours=4 * index),
                open=close - 0.5,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=1000.0,
            )
        )
    return output


def state(rank: float, current: float = -0.002) -> v15.FundingState:
    return v15.FundingState(
        current=current,
        percentile_rank=rank,
        fifth=-0.003,
        fifteenth=-0.0015,
        rolling_median=0.0001,
    )


def test_states_require_full_calendar_history() -> None:
    start = datetime(2023, 1, 1)
    states = v15.build_funding_states(funding_series(start, 140, 8, -0.002))
    assert states
    assert min(states) == start + timedelta(days=126, hours=20)


def test_missing_settlement_buckets_are_zero_cashflow() -> None:
    start = datetime(2023, 1, 1)
    states = v15.build_funding_states(funding_series(start, 140, 8, -0.002))
    latest = states[max(states)]
    assert latest.current == pytest.approx(-0.001)
    assert latest.rolling_median == pytest.approx(-0.001)


def test_future_funding_cannot_change_earlier_state() -> None:
    start = datetime(2023, 1, 1)
    original = funding_series(start, 140, 8, -0.002)
    left = v15.build_funding_states(original)
    anchor = sorted(left)[10]
    extended = dict(original)
    future_start = max(original) + timedelta(hours=8)
    extended.update(
        {
            future_start + timedelta(hours=8 * index): 10.0
            for index in range(100)
        }
    )
    right = v15.build_funding_states(extended)
    assert right[anchor] == left[anchor]


def test_primary_enforces_current_universe_bottom_three(monkeypatch: pytest.MonkeyPatch) -> None:
    start = datetime(2024, 1, 1)
    symbols = ("A", "B", "C", "D")
    prior = {symbol: recovery_candles(start) for symbol in symbols}
    states = {
        "A": state(0.01, -0.004),
        "B": state(0.02, -0.003),
        "C": state(0.03, -0.002),
        "D": state(0.04, -0.001),
    }
    monkeypatch.setattr(v15, "funding_state", lambda store, symbol, as_of: states[symbol])
    store = base.ExternalStore({}, {}, {}, {}, "test")
    primary = v15.funding_state_candidates(prior, store, ("primary",))
    without_cross = v15.funding_state_candidates(prior, store, ("without_cross",))
    assert set(primary) == {"A", "B", "C"}
    assert set(without_cross) == set(symbols)


def test_primary_requires_two_price_recovery_confirmations(monkeypatch: pytest.MonkeyPatch) -> None:
    start = datetime(2024, 1, 1)
    candles = recovery_candles(start, final_close=79.0)
    monkeypatch.setattr(v15, "funding_state", lambda store, symbol, as_of: state(0.01))
    store = base.ExternalStore({}, {}, {}, {}, "test")
    prior = {"A": candles}
    assert v15.funding_state_candidates(prior, store, ("primary",)) == {}
    assert set(v15.funding_state_candidates(prior, store, ("without_recovery",))) == {"A"}


def test_deep_extreme_uses_five_percent_state(monkeypatch: pytest.MonkeyPatch) -> None:
    start = datetime(2024, 1, 1)
    prior = {
        "A": recovery_candles(start),
        "B": recovery_candles(start),
    }
    states = {"A": state(0.05), "B": state(0.051)}
    monkeypatch.setattr(v15, "funding_state", lambda store, symbol, as_of: states[symbol])
    store = base.ExternalStore({}, {}, {}, {}, "test")
    assert set(v15.funding_state_candidates(prior, store, ("deep_extreme",))) == {"A"}


def test_legacy_variant_delegates_to_frozen_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {"A": base.Candidate("A", "funding", 1.0, 2.0)}
    monkeypatch.setattr(
        v15,
        "_ORIGINAL_SIGNAL_CANDIDATES",
        lambda prior, store, sleeves: expected if sleeves == ("funding",) else {},
    )
    result = v15.funding_state_candidates({}, base.ExternalStore({}, {}, {}, {}, "test"), ("legacy",))
    assert result is expected


def test_exit_when_funding_state_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    candles = recovery_candles(datetime(2024, 1, 1))
    recovered = v15.FundingState(-0.0001, 0.60, -0.003, -0.0015, -0.0002)
    monkeypatch.setattr(v15, "funding_state", lambda store, symbol, as_of: recovered)
    position = base.PositionState(
        quantity=1.0,
        average_price=80.0,
        entry_time=candles[-10].timestamp,
        entry_index=170,
        sleeve="funding",
        entry_atr=2.0,
        highest_close=85.0,
    )
    assert v15.funding_state_exit(
        position,
        candles,
        base.ExternalStore({}, {}, {}, {}, "test"),
        180,
        "A",
    )


def test_frozen_portfolio_limits() -> None:
    assert v15.CONFIG.max_positions == 2
    assert v15.CONFIG.max_asset_weight == pytest.approx(0.25)
    assert v15.CONFIG.min_cash_reserve == pytest.approx(0.50)
    assert v15.CONFIG.target_volatility == pytest.approx(0.25)
    assert v15.CONFIG.max_positions * v15.CONFIG.max_asset_weight <= 1.0 - v15.CONFIG.min_cash_reserve


def test_holdout_is_locked_without_accepted_discovery(tmp_path) -> None:
    discovery = tmp_path / "discovery.json"
    discovery.write_text(
        '{"accepted": false, "eligible_for_holdout": false}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="holdout is locked"):
        v15.evaluate_holdout(
            tmp_path / "prices",
            tmp_path / "external",
            discovery,
        )
