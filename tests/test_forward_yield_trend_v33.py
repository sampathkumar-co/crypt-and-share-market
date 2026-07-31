from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradebot.research import forward_yield_trend_v33 as v33
from tradebot.research import forward_yield_trend_v33_sources as sources
from tradebot.research import historical_yield_trend_v31 as v31


def _feature(score: float = 10.0) -> v31.Features:
    return v31.Features(
        return_1=0.01,
        return_5=0.05,
        return_20=0.10,
        return_60=0.20,
        return_120=0.30,
        return_200=0.40,
        volatility_20=0.02,
        sma_50=80.0,
        sma_100=80.0,
        sma_200=80.0,
        close=100.0,
        drawdown_20=-0.01,
        trend_score=score,
    )


def test_latest_completed_day_uses_only_prior_utc_day() -> None:
    now = datetime(2026, 7, 31, 16, 30, tzinfo=timezone.utc)
    assert sources.latest_completed_day(now) == datetime(
        2026, 7, 30, tzinfo=timezone.utc
    )


def test_first_qualified_observation_enters_with_ten_percent(monkeypatch) -> None:
    def target(*args, **kwargs):
        return {"BTC": 0.10}, ("BTC",), "trend", 0

    monkeypatch.setattr(v31, "_target", target)
    result = v33.decide(
        {"BTC": _feature(), "ETH": _feature(5.0)},
        previous=None,
        contiguous=True,
    )
    assert result["action"] == "ENTER"
    assert result["trade_required"] is True
    assert result["recommended_target_weights"] == {"BTC": 0.10}
    assert result["minimum_cash_target"] == 0.90
    assert result["state_after"] == {
        "sleeve": "trend",
        "selected_assets": ["BTC"],
        "age": 0,
    }


def test_non_due_day_holds_without_republishing_target(monkeypatch) -> None:
    def target(*args, **kwargs):
        return {"BTC": 0.08}, ("BTC",), "trend", 5

    monkeypatch.setattr(v31, "_target", target)
    previous = {
        "state_after": {
            "sleeve": "trend",
            "selected_assets": ["BTC"],
            "age": 4,
        }
    }
    result = v33.decide(
        {"BTC": _feature(), "ETH": _feature(5.0)},
        previous=previous,
        contiguous=True,
    )
    assert result["action"] == "HOLD_NO_TRADE"
    assert result["trade_required"] is False
    assert result["recommended_target_weights"] is None
    assert result["minimum_cash_target"] is None
    assert result["state_after"]["age"] == 5


def test_daily_risk_off_exits_even_before_rebalance(monkeypatch) -> None:
    def target(*args, **kwargs):
        return {}, (), "cash", 0

    monkeypatch.setattr(v31, "_target", target)
    previous = {
        "state_after": {
            "sleeve": "trend",
            "selected_assets": ["ETH"],
            "age": 2,
        }
    }
    result = v33.decide(
        {"BTC": _feature(), "ETH": _feature()},
        previous=previous,
        contiguous=True,
    )
    assert result["action"] == "EXIT"
    assert result["trade_required"] is True
    assert result["recommended_target_weights"] == {}
    assert result["minimum_cash_target"] == 1.0


def test_missing_prior_calendar_day_resets_without_target() -> None:
    result = v33.decide(
        {"BTC": _feature(), "ETH": _feature()},
        previous={"state_after": {"sleeve": "trend", "selected_assets": ["BTC"], "age": 3}},
        contiguous=False,
    )
    assert result["action"] == "GAP_RESET_NO_TRADE"
    assert result["trade_required"] is False
    assert result["recommended_target_weights"] is None
    assert result["state_after"]["sleeve"] == "cash"


def test_load_previous_requires_exact_calendar_day(tmp_path: Path) -> None:
    completed = datetime(2026, 7, 30, tzinfo=timezone.utc)
    old = tmp_path / "2026-07-28.json"
    old.write_text(
        json.dumps({"state_after": {"sleeve": "cash", "selected_assets": [], "age": 0}}) + "\n",
        encoding="utf-8",
    )
    payload, digest, contiguous = v33.load_previous(tmp_path, completed)
    assert payload is not None
    assert digest == hashlib.sha256(old.read_bytes()).hexdigest()
    assert contiguous is False


def test_build_observation_has_one_day_operational_latency(
    monkeypatch,
    tmp_path: Path,
) -> None:
    completed = datetime(2026, 7, 30, tzinfo=timezone.utc)
    bars = {asset: {} for asset in v31.ASSETS}
    monkeypatch.setattr(
        sources,
        "fetch_coinbase_history",
        lambda day: (bars, [], {"BTC": "b" * 64, "ETH": "e" * 64}),
    )
    monkeypatch.setattr(
        sources,
        "fetch_h15_evidence",
        lambda day: (
            {
                "series_id": "H15/H15/RIFLGFCM03_N.B",
                "normalized_sha256": "c" * 64,
                "observation_count": 1,
                "latest_known_date": "2026-07-29",
                "latest_known_annual_rate": 0.04,
            },
            {"key": "cash:DGS3MO", "raw_sha256": "d" * 64},
        ),
    )
    monkeypatch.setattr(
        v31,
        "build_features",
        lambda bars, dates: {
            completed: {"BTC": _feature(), "ETH": _feature(5.0)}
        },
    )
    monkeypatch.setattr(
        v31,
        "_target",
        lambda *args, **kwargs: ({"BTC": 0.10}, ("BTC",), "trend", 0),
    )
    observation, manifest = v33.build_observation(
        as_of=datetime(2026, 7, 31, 16, 0, tzinfo=timezone.utc),
        history_folder=tmp_path,
    )
    assert observation["completed_candle_date_utc"] == "2026-07-30"
    assert observation["earliest_eligible_effective_open_utc"] == "2026-08-01T00:00:00Z"
    assert observation["operational_latency_days"] == 1
    assert observation["authorizes_trading"] is False
    assert observation["authorizes_shadow_paper"] is False
    assert manifest["observation_report_sha256"] == observation["report_sha256"]
    forbidden = {"pnl", "profit", "drawdown", "sharpe", "benchmark_return"}
    assert forbidden.isdisjoint(observation)
