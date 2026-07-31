from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradebot.research import historical_yield_trend_v31 as v31
from tradebot.research import historical_yield_trend_v31_runner as runner


def feature(**overrides: float) -> v31.Features:
    values = dict(
        return_1=0.01,
        return_5=0.03,
        return_20=0.10,
        return_60=0.20,
        return_120=0.30,
        return_200=0.40,
        volatility_20=0.02,
        sma_50=90.0,
        sma_100=85.0,
        sma_200=80.0,
        close=100.0,
        drawdown_20=-0.02,
        trend_score=10.0,
    )
    values.update(overrides)
    return v31.Features(**values)


def model(**overrides: object) -> v31.ModelSpec:
    values = dict(
        sma_length=100,
        rebalance_days=5,
        top_n=2,
        maximum_exposure=0.20,
        volatility_target=0.02,
        drawdown_brake=0.10,
    )
    values.update(overrides)
    return v31.ModelSpec(**values)


def test_frozen_grid_has_exactly_64_unique_models() -> None:
    assert len(v31.MODEL_GRID) == 64
    assert len({item.model_id for item in v31.MODEL_GRID}) == 64
    assert {item.sma_length for item in v31.MODEL_GRID} == {100, 200}
    assert {item.rebalance_days for item in v31.MODEL_GRID} == {5, 10}
    assert {item.top_n for item in v31.MODEL_GRID} == {1, 2}
    assert {item.maximum_exposure for item in v31.MODEL_GRID} == {0.10, 0.20}
    assert {item.volatility_target for item in v31.MODEL_GRID} == {0.02, 0.03}
    assert {item.drawdown_brake for item in v31.MODEL_GRID} == {0.10, 0.20}


def test_fred_parser_accepts_current_and_legacy_date_headers() -> None:
    current = (
        b"observation_date,DGS3MO\n"
        b"2017-08-31,1.00\n"
        b"2017-09-01,.\n"
        b"2017-09-04,1.10\n"
    )
    legacy = b"DATE,DGS3MO\n2017-08-31,1.00\n"

    current_rates = runner.parse_cash_rates_flexible(current)
    legacy_rates = runner.parse_cash_rates_flexible(legacy)

    assert current_rates == {
        datetime(2017, 8, 31, tzinfo=timezone.utc): 0.01,
        datetime(2017, 9, 4, tzinfo=timezone.utc): 0.011,
    }
    assert legacy_rates == {
        datetime(2017, 8, 31, tzinfo=timezone.utc): 0.01
    }


def test_daily_cash_return_uses_only_prior_known_rate_and_weekend_carry() -> None:
    rates = {
        datetime(2017, 8, 31, tzinfo=timezone.utc): 0.01,
        datetime(2017, 9, 4, tzinfo=timezone.utc): 0.02,
    }
    dates = [
        datetime(2017, 9, 1, tzinfo=timezone.utc),
        datetime(2017, 9, 2, tzinfo=timezone.utc),
        datetime(2017, 9, 3, tzinfo=timezone.utc),
        datetime(2017, 9, 4, tzinfo=timezone.utc),
        datetime(2017, 9, 5, tzinfo=timezone.utc),
    ]

    returns = v31.build_daily_cash_returns(rates, dates)
    one_percent = (1.01 ** (1.0 / 365.0)) - 1.0
    two_percent = (1.02 ** (1.0 / 365.0)) - 1.0

    assert all(abs(returns[day] - one_percent) < 1e-15 for day in dates[:4])
    assert abs(returns[dates[4]] - two_percent) < 1e-15


def test_risk_on_selects_btc_and_eth_with_20_percent_maximum() -> None:
    payload = {
        "BTC": feature(trend_score=5.0),
        "ETH": feature(trend_score=7.0),
    }
    weights, assets, sleeve, age = v31._target(
        model(), payload, (), "cash", 0
    )

    assert assets == ("ETH", "BTC")
    assert sleeve == "trend"
    assert age == 0
    assert weights == {"ETH": 0.10, "BTC": 0.10}


def test_risk_off_is_full_yield_bearing_cash() -> None:
    payload = {
        "BTC": feature(return_60=-0.01),
        "ETH": feature(return_60=-0.01),
    }
    weights, assets, sleeve, age = v31._target(
        model(), payload, (), "cash", 0
    )
    assert weights == {}
    assert assets == ()
    assert sleeve == "cash"
    assert age == 0


def test_volatility_and_drawdown_scaling_never_exceed_cap() -> None:
    payload = {
        "BTC": feature(volatility_20=0.04, drawdown_20=-0.12),
        "ETH": feature(volatility_20=0.04),
    }
    weights, _, sleeve, _ = v31._target(
        model(volatility_target=0.02, drawdown_brake=0.10),
        payload,
        (),
        "cash",
        0,
    )
    assert sleeve == "trend"
    assert abs(sum(weights.values()) - 0.05) < 1e-12


def test_discovery_and_verification_periods_are_frozen() -> None:
    assert len(v31.DISCOVERY_PERIODS) == 10
    assert v31.DISCOVERY_PERIODS[0].name == "2018-Q3"
    assert v31.DISCOVERY_PERIODS[-1].name == "2020-Q4"
    assert [item.name for item in v31.VERIFICATION_PERIODS] == [
        "2021", "2022", "2023", "2024", "2025"
    ]
    for left, right in zip(
        v31.VERIFICATION_PERIODS,
        v31.VERIFICATION_PERIODS[1:],
    ):
        assert left.end + timedelta(days=1) == right.start


def test_runner_restores_original_cash_parser(monkeypatch) -> None:
    captured: dict[str, bool] = {}

    def fake_run(*, max_workers: int):
        captured["patched"] = (
            v31.parse_cash_rates is runner.parse_cash_rates_flexible
        )
        return {
            "fingerprints": {
                "protocol_sha256": "a",
                "addendum_sha256": "b",
                "implementation_sha256": "c",
                "chosen_model_sha256": "d",
            },
            "report_sha256": "stale",
        }

    original = v31.parse_cash_rates
    monkeypatch.setattr(v31, "run_overlay", fake_run)
    report = runner.run_guarded_overlay(max_workers=2)

    assert captured["patched"] is True
    assert v31.parse_cash_rates is original
    assert report["cash_source_policy"] == runner.CASH_SOURCE_POLICY
    assert report["fingerprints"]["runner_sha256"]
    assert report["report_sha256"] != "stale"
