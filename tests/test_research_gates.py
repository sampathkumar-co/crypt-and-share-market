from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tradebot.backtest.paper_trader import BacktestConfig, PaperTrader
from tradebot.backtest.regime import classify_regime, regime_allows_strategy
from tradebot.backtest.research_gate import (
    GATE_SCHEMA_VERSION,
    ResearchGateConfig,
    evaluate_research_gate,
    implementation_fingerprint,
    implementation_version,
    independent_train_test_windows,
    validate_forward_gate,
    write_gate_report,
)
from tradebot.models import Action, Candle, Market, Signal
from tradebot.strategies.base import Strategy


class AlwaysBuy(Strategy):
    def generate_signal(self, candles: list[Candle]) -> Signal:
        return Signal(Action.BUY, 0.9, "always", 0.9, 0.2)


def candles(count: int, *, start: float = 100.0, step: float = 1.0) -> list[Candle]:
    output = []
    price = start
    for index in range(count):
        open_price = price
        price += step
        output.append(
            Candle(
                datetime(2024, 1, 1) + timedelta(days=index),
                open_price,
                max(open_price, price) * 1.01,
                min(open_price, price) * 0.99,
                price,
                10_000 + index * 100,
            )
        )
    return output


def write_csv(path, history: list[Candle]) -> None:
    lines = ["timestamp,open,high,low,close,volume"]
    lines.extend(
        f"{c.timestamp.isoformat()},{c.open},{c.high},{c.low},{c.close},{c.volume}"
        for c in history
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def passing_report(strategy: str = "breakout") -> dict:
    return {
        "schema_version": GATE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market": "crypto",
        "accepted": True,
        "eligible_for_continuous_paper": True,
        "passed_strategies": [strategy],
        "dataset_fingerprint": "abc",
        "implementation_version": implementation_version(),
        "implementation_fingerprint": implementation_fingerprint(),
        "symbols": ["BTCUSDT"],
        "forward_configurations": {
            strategy: {
                "strategy": strategy,
                "strategy_parameters": {"lookback": 10, "buffer": 0.002}
                if strategy == "breakout"
                else {"lookback": 5, "min_return": 0.015, "volume_multiplier": 1.15},
                "execution_parameters": {
                    "intrabar_policy": "worst_case",
                    "min_holding_bars": 5,
                    "max_holding_bars": 45,
                    "cooldown_bars": 3,
                    "exit_confirmation_bars": 2,
                    "trailing_stop_pct": 0.04,
                    "breakeven_trigger_pct": 0.025,
                    "use_regime_filter": True,
                },
            }
        },
    }


def test_regime_filter_allows_trend_strategies_only_in_bull_regime():
    bull = classify_regime(candles(40, step=1.5))
    bear = classify_regime(candles(40, start=200, step=-1.5))
    assert bull.name == "bull_trending_up"
    assert regime_allows_strategy("momentum", bull)
    assert not regime_allows_strategy("mean_reversion", bull)
    assert bear.name in {"bear_trending_down", "high_volatility_or_drawdown"}
    assert not regime_allows_strategy("momentum", bear)


def test_independent_unseen_windows_do_not_overlap():
    history = candles(100)
    windows = independent_train_test_windows(history, train_size=40, test_size=20)
    assert len(windows) == 3
    unseen_ranges = [(test[0].timestamp, test[-1].timestamp) for _, test in windows]
    assert unseen_ranges[0][1] < unseen_ranges[1][0]
    assert unseen_ranges[1][1] < unseen_ranges[2][0]


def test_backtest_reports_cash_buy_hold_churn_and_cost_metrics():
    result = PaperTrader(
        Market.CRYPTO,
        AlwaysBuy(),
        config=BacktestConfig(
            min_holding_bars=3,
            max_holding_bars=20,
            cooldown_bars=2,
            exit_confirmation_bars=2,
        ),
    ).run("TEST", candles(45, step=1.2))
    assert result.cash_return == 0.0
    assert isinstance(result.buy_and_hold_return, float)
    assert result.trades_per_100_bars >= 0.0
    assert result.turnover >= 0.0
    assert 0.0 <= result.cost_drag_ratio <= 1.0
    assert result.average_holding_bars >= 0.0


def test_unseen_churn_denominator_excludes_disabled_warmup_bars():
    result = PaperTrader(
        Market.CRYPTO,
        AlwaysBuy(),
        config=BacktestConfig(warmup_bars=10, min_holding_bars=2, max_holding_bars=12),
    ).run("TEST", candles(45, step=1.2), trade_start_index=30)
    assert result.trades
    assert result.trades_per_100_bars == pytest.approx(len(result.trades) / 15 * 100.0)


def test_gate_report_blocks_continuous_paper_when_not_accepted(tmp_path):
    report = {
        "schema_version": GATE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market": "crypto",
        "accepted": False,
        "eligible_for_continuous_paper": False,
        "passed_strategies": [],
        "implementation_version": implementation_version(),
        "implementation_fingerprint": implementation_fingerprint(),
        "forward_configurations": {},
    }
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="did not pass"):
        validate_forward_gate(path, strategy_name="momentum")


def test_gate_report_allows_only_exact_passing_strategy_configuration(tmp_path):
    report = passing_report("breakout")
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    payload = validate_forward_gate(path, strategy_name="breakout")
    assert payload["forward_configurations"]["breakout"]["execution_parameters"]["max_holding_bars"] == 45
    with pytest.raises(ValueError, match="momentum"):
        validate_forward_gate(path, strategy_name="momentum")


def test_gate_report_rejects_changed_implementation(tmp_path):
    report = passing_report("breakout")
    report["implementation_fingerprint"] = "0" * 64
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="implementation changed"):
        validate_forward_gate(path, strategy_name="breakout")


def test_research_gate_evaluates_every_strategy_and_fails_closed_on_too_little_data(tmp_path):
    folder = tmp_path / "crypto"
    folder.mkdir()
    write_csv(folder / "BTCUSDT.csv", candles(55, step=0.8))
    report = evaluate_research_gate(
        folder,
        config=ResearchGateConfig(
            train_size=30,
            test_size=10,
            min_independent_periods=3,
            max_candidates_per_strategy=2,
        ),
    )
    assert {item.strategy for item in report.strategies} == {
        "momentum",
        "breakout",
        "mean_reversion",
    }
    assert not report.accepted
    assert not report.eligible_for_continuous_paper
    assert report.forward_configurations == {}
    assert len(report.implementation_fingerprint) == 64

    output = tmp_path / "gate-report.json"
    write_gate_report(output, report)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == GATE_SCHEMA_VERSION
    assert payload["paper_only"] is True
