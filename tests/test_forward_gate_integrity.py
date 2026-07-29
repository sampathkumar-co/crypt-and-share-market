from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradebot.backtest.paper_live import PaperLiveCryptoBot
from tradebot.backtest.research_gate import (
    GATE_SCHEMA_VERSION,
    implementation_fingerprint,
    implementation_version,
)
from tradebot.models import Candle


class EmptyProvider:
    def fetch_symbol(self, symbol: str, interval: str = "1m", days: int = 60):
        return []


def history(count: int = 35) -> list[Candle]:
    output = []
    price = 100.0
    for index in range(count):
        opened = price
        price += 1.0
        output.append(
            Candle(
                datetime(2025, 1, 1) + timedelta(minutes=index),
                opened,
                price * 1.01,
                opened * 0.99,
                price,
                10_000 + index * 100,
            )
        )
    return output


def gate_report() -> dict:
    return {
        "schema_version": GATE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market": "crypto",
        "accepted": True,
        "eligible_for_continuous_paper": True,
        "passed_strategies": ["momentum"],
        "dataset_fingerprint": "abc123",
        "implementation_version": implementation_version(),
        "implementation_fingerprint": implementation_fingerprint(),
        "symbols": ["BTCUSDT"],
        "forward_configurations": {
            "momentum": {
                "strategy": "momentum",
                "strategy_parameters": {
                    "lookback": 8,
                    "min_return": 0.025,
                    "volume_multiplier": 1.35,
                },
                "execution_parameters": {
                    "intrabar_policy": "worst_case",
                    "min_holding_bars": 8,
                    "max_holding_bars": 60,
                    "cooldown_bars": 5,
                    "exit_confirmation_bars": 3,
                    "trailing_stop_pct": 0.05,
                    "breakeven_trigger_pct": 0.03,
                    "use_regime_filter": True,
                },
            }
        },
    }


def write_gate(tmp_path) -> str:
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(gate_report()), encoding="utf-8")
    return str(path)



def write_ledger(tmp_path) -> str:
    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "deployment_mode": "forward_paper_candidate",
                "paper_only": True,
                "approved_strategies": ["momentum"],
                "continuous_paper_authorized": True,
                "live_trading_authorized": False,
                "experiments": [],
                "decision": "FORWARD_PAPER_ONLY",
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def test_continuous_authorization_loads_exact_frozen_configuration(tmp_path):
    bot = PaperLiveCryptoBot(
        ["BTCUSDT"],
        "1m",
        100000,
        tmp_path / "state.json",
        provider=EmptyProvider(),
    )
    bot.authorize_continuous(write_gate(tmp_path), research_ledger_path=write_ledger(tmp_path))

    assert bot.continuous_authorized
    assert bot.strategy.lookback == 8
    assert bot.strategy.min_return == 0.025
    assert bot.strategy.volume_multiplier == 1.35
    assert bot.min_holding_loops == 8
    assert bot.max_holding_loops == 60
    assert bot.cooldown_loops == 5
    assert bot.exit_confirmation_loops == 3
    assert bot.trailing_stop_pct == 0.05
    assert bot.breakeven_trigger_pct == 0.03
    assert bot.state.gate_authorization["forward_configuration"]["strategy"] == "momentum"


def test_pending_forward_signal_expires_when_more_than_one_candle_was_missed(tmp_path):
    bot = PaperLiveCryptoBot(
        ["BTCUSDT"],
        "1m",
        100000,
        tmp_path / "state.json",
        provider=EmptyProvider(),
    )
    bot.authorize_continuous(write_gate(tmp_path), research_ledger_path=write_ledger(tmp_path))
    candles = history(37)
    signal_time = candles[-3].timestamp
    bot.histories["BTCUSDT"] = candles
    bot.state.pending_entry = {
        "symbol": "BTCUSDT",
        "signal_time": signal_time.isoformat(),
        "signal": {
            "action": "BUY",
            "score": 0.9,
            "reason": "queued",
            "confidence": 0.9,
            "risk_score": 0.2,
        },
        "entry_reason": "queued",
        "implementation_fingerprint": implementation_fingerprint(),
    }

    entered, reason = bot._execute_pending_entry()

    assert not entered
    assert "expired" in reason.lower()
    assert bot.state.pending_entry is None
    assert bot.state.open_position is None


def test_continuous_authorization_refuses_uncovered_symbol(tmp_path):
    bot = PaperLiveCryptoBot(
        ["ETHUSDT"],
        "1m",
        100000,
        tmp_path / "state.json",
        provider=EmptyProvider(),
    )
    try:
        bot.authorize_continuous(write_gate(tmp_path), research_ledger_path=write_ledger(tmp_path))
        assert False, "authorization should reject a symbol outside the gate dataset"
    except ValueError as exc:
        assert "covered" in str(exc)


def test_continuous_authorization_requires_research_ledger(tmp_path):
    bot = PaperLiveCryptoBot(
        ["BTCUSDT"],
        "1m",
        100000,
        tmp_path / "state.json",
        provider=EmptyProvider(),
    )
    try:
        bot.authorize_continuous(
            write_gate(tmp_path),
            research_ledger_path=tmp_path / "missing-ledger.json",
        )
        assert False, "missing research ledger must deny continuous paper"
    except ValueError as exc:
        assert "fails closed" in str(exc)
    assert not bot.continuous_authorized
    assert bot.state.gate_authorization is None


def test_failed_revalidation_clears_continuous_authorization(tmp_path):
    gate = write_gate(tmp_path)
    ledger = write_ledger(tmp_path)
    bot = PaperLiveCryptoBot(
        ["BTCUSDT"],
        "1m",
        100000,
        tmp_path / "state.json",
        provider=EmptyProvider(),
    )
    bot.authorize_continuous(gate, research_ledger_path=ledger)
    assert bot.continuous_authorized

    Path(ledger).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "deployment_mode": "research_only",
                "paper_only": True,
                "approved_strategies": [],
                "continuous_paper_authorized": False,
                "live_trading_authorized": False,
                "experiments": [],
            }
        ),
        encoding="utf-8",
    )
    try:
        bot.authorize_continuous(gate, research_ledger_path=ledger)
        assert False, "revoked ledger must stop continuous authorization"
    except ValueError as exc:
        assert "not authorized" in str(exc)
    assert not bot.continuous_authorized


def test_continuous_loop_revalidates_before_each_iteration(tmp_path):
    bot = PaperLiveCryptoBot(
        ["BTCUSDT"],
        "1m",
        100000,
        tmp_path / "state.json",
        provider=EmptyProvider(),
    )
    calls = []

    def authorize(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) >= 2:
            raise RuntimeError("authorization revoked")
        bot.continuous_authorized = True
        return {}

    bot.authorize_continuous = authorize  # type: ignore[method-assign]
    bot.run_once = lambda: (_ for _ in ()).throw(AssertionError("run_once must not execute after revocation"))  # type: ignore[method-assign]
    try:
        bot.run_continuous(
            sleep_seconds=1,
            gate_report_path=tmp_path / "gate.json",
            research_ledger_path=tmp_path / "ledger.json",
        )
        assert False, "revocation should stop the loop"
    except RuntimeError as exc:
        assert "revoked" in str(exc)
    assert len(calls) == 2
