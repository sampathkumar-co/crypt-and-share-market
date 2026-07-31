from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradebot.research import historical_proxy_screen_v25 as v25
from tradebot.research import historical_yield_trend_v31 as v31
from tradebot.research import historical_yield_trend_integrity_v312 as audit
from tradebot.research import historical_yield_trend_scheduled_execution_v312 as execution


def _bar(day: datetime, price: float) -> v25.HourlyBar:
    return v25.HourlyBar(
        hour=day,
        open=price,
        high=price * 1.01,
        low=price * 0.99,
        close=price,
        quote_volume=1_000_000.0,
        taker_buy_quote_volume=0.0,
    )


def _feature(*, score: float, risk_on: bool = True) -> v31.Features:
    return v31.Features(
        return_1=0.01,
        return_5=0.05,
        return_20=0.10,
        return_60=0.20 if risk_on else -0.01,
        return_120=0.30 if risk_on else -0.01,
        return_200=0.40 if risk_on else -0.01,
        volatility_20=0.02,
        sma_50=80.0,
        sma_100=80.0,
        sma_200=80.0,
        close=100.0,
        drawdown_20=-0.01,
        trend_score=score,
    )


def _fixture(days: int = 14):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = {asset: {} for asset in v31.ASSETS}
    for index in range(days + 2):
        day = start + timedelta(days=index)
        bars["BTC"][day] = _bar(day, 100.0 * (1.01**index))
        bars["ETH"][day] = _bar(day, 100.0)
    features = {}
    for index in range(-1, days + 1):
        day = start + timedelta(days=index)
        features[day] = {
            "BTC": _feature(score=10.0),
            "ETH": _feature(score=5.0),
        }
    cash = {
        start + timedelta(days=index): 0.0
        for index in range(days + 1)
    }
    return start, bars, features, cash


def test_corrected_engine_preserves_drift_until_due_rebalance() -> None:
    start, bars, features, cash = _fixture(days=12)
    end = start + timedelta(days=11)
    corrected = execution.simulate_scheduled(
        audit.FROZEN_MODEL,
        bars,
        features,
        cash,
        start,
        end,
        0.002,
    )
    inherited = v31.simulate(
        audit.FROZEN_MODEL,
        bars,
        features,
        cash,
        start,
        end,
        0.002,
    )
    # Entry, one due rebalance and terminal liquidation are the only possible
    # corrected actions. The inherited path creates additional daily actions.
    assert corrected.crypto_action_days <= 3
    assert inherited.crypto_action_days > corrected.crypto_action_days
    assert corrected.selected_assets == ["BTC"]


def test_daily_risk_off_still_exits_before_scheduled_rebalance() -> None:
    start, bars, features, cash = _fixture(days=5)
    for offset in range(0, 4):
        signal_day = start + timedelta(days=offset)
        features[signal_day] = {
            "BTC": _feature(score=10.0, risk_on=False),
            "ETH": _feature(score=5.0, risk_on=False),
        }
    result = execution.simulate_scheduled(
        audit.FROZEN_MODEL,
        bars,
        features,
        cash,
        start,
        start + timedelta(days=3),
        0.002,
    )
    assert result.crypto_action_days == 2
    assert result.selected_assets == ["BTC"]
    assert result.crypto_turnover > 0.19


def test_frozen_integrity_model_matches_v31_selected_model() -> None:
    assert audit.FROZEN_MODEL.model_id == audit.EXPECTED_MODEL_ID
    assert audit.FROZEN_MODEL.sma_length == 100
    assert audit.FROZEN_MODEL.rebalance_days == 10
    assert audit.FROZEN_MODEL.top_n == 1
    assert audit.FROZEN_MODEL.maximum_exposure == 0.10
    assert audit.FROZEN_MODEL.volatility_target == 0.02
    assert audit.FROZEN_MODEL.drawdown_brake == 0.20


def test_integrity_gate_accepts_one_exact_cash_year() -> None:
    years = ["2021", "2022", "2023", "2024", "2025"]
    active = ["2021", "2023", "2024", "2025"]
    cash = {year: 0.02 for year in years}
    returns = {
        "2021": 0.04,
        "2022": 0.02,
        "2023": 0.04,
        "2024": 0.05,
        "2025": 0.04,
    }
    excess = {year: returns[year] - cash[year] for year in years}
    summary = {
        "window_returns": returns,
        "cash_window_returns": cash,
        "excess_window_returns": excess,
        "window_action_days": {
            year: (5 if year in active else 0) for year in years
        },
        "active_years": active,
        "inactive_years": ["2022"],
        "active_year_count": 4,
        "selected_assets": ["BTC", "ETH"],
        "crypto_action_days": 20,
        "net_compounded_return": 0.20,
        "cash_benchmark_compounded_return": 0.10,
        "excess_compounded_return": 0.10,
        "maximum_drawdown": 0.02,
        "maximum_positive_asset_share": 0.60,
        "maximum_positive_year_share": 0.40,
    }
    gates = execution.evaluate_integrity_gates(summary, dict(summary))
    assert all(gates.values())
