from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from tradebot.research.market_state_router import (
    ASSETS,
    SnapshotFrame,
    _global_controls,
    canonical_json,
    evaluate_market_state_router,
    load_forward_snapshots,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _asset_record(
    *,
    price: float,
    open_interest: float = 100.0,
    basis_bps: float = 0.0,
    funding: float = 0.0,
    spot_flow: float = 0.0,
    spot_book: float = 0.0,
    perp_flow: float = 0.0,
):
    mark = price * (1.0 + basis_bps / 10_000.0)
    return {
        "spot_quote": {"available": True, "mid": price},
        "spot_book": {"available": True, "imbalance": spot_book},
        "spot_trade_flow": {"available": True, "taker_imbalance": spot_flow},
        "perp_state": {
            "available": True,
            "funding": funding,
            "open_interest_base": open_interest,
            "mark": mark,
        },
        "perp_trade_flow": {"available": True, "reported_side_imbalance": perp_flow},
        "cross_venue": {"available": True, "spot_perp_basis_bps": basis_bps},
    }


def _global_state(
    *,
    vix: float = 18.0,
    dollar: float = 120.0,
    usdt: float = 190_000_000_000.0,
    usdc: float = 70_000_000_000.0,
    macro_staleness: int = 0,
    stable_staleness: int = 0,
):
    def record(metric: str, value: float, staleness: int):
        return {"available": True, "staleness_days": staleness, "values": {metric: value}}

    return {
        "available": True,
        "fred": {
            "VIXCLS": record("VIXCLS", vix, macro_staleness),
            "DTWEXBGS": record("DTWEXBGS", dollar, macro_staleness),
            "DGS10": record("DGS10", 4.0, macro_staleness),
        },
        "coinmetrics": {
            "USDT": record("CapMrktCurUSD", usdt, stable_staleness),
            "USDC": record("CapMrktCurUSD", usdc, stable_staleness),
        },
    }


def _frame(index: int, *, asset_changes=None, global_changes=None, captured_minute: int = 17):
    hour = START + timedelta(hours=index)
    assets = {}
    for offset, asset in enumerate(ASSETS):
        base_price = 100.0 + offset * 10.0
        values = {
            "price": base_price,
            "open_interest": 100.0,
            "basis_bps": 0.0,
            "funding": 0.0,
            "spot_flow": 0.0,
            "spot_book": 0.0,
            "perp_flow": 0.0,
        }
        if asset_changes and asset in asset_changes:
            values.update(asset_changes[asset])
        assets[asset] = _asset_record(**values)
    globals_payload = _global_state(**(global_changes or {}))
    captured = hour + timedelta(minutes=captured_minute)
    snapshot_id = captured.strftime("%Y%m%dT%H%M%S.%fZ")
    raw = {
        "schema_version": "2.0",
        "paper_only": True,
        "authorizes_trading": False,
        "snapshot_id": snapshot_id,
        "captured_at_utc": captured.isoformat().replace("+00:00", "Z"),
        "hour_bucket_utc": hour.isoformat().replace("+00:00", "Z"),
        "assets": assets,
        "global": globals_payload,
        "liquidation_events": {"available": False, "reason": "test", "events": []},
        "source_errors": {},
    }
    record_sha = hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest()
    return SnapshotFrame(hour, captured, snapshot_id, record_sha, assets, globals_payload, f"{snapshot_id}.json"), {**raw, "record_sha256": record_sha}


def _history(current_changes=None, *, series_prices=None, indexed_changes=None, global_current=None, global_week=None):
    frames = []
    for index in range(169):
        changes = {}
        if series_prices:
            for asset, prices in series_prices.items():
                changes[asset] = {"price": prices[index]}
        if indexed_changes and index in indexed_changes:
            for asset, values in indexed_changes[index].items():
                changes.setdefault(asset, {}).update(values)
        globals_for_hour = None
        if index == 0 and global_week:
            globals_for_hour = global_week
        if index == 168 and global_current:
            globals_for_hour = global_current
        if index == 168 and current_changes:
            for asset, values in current_changes.items():
                changes.setdefault(asset, {}).update(values)
        frame, _ = _frame(index, asset_changes=changes, global_changes=globals_for_hour)
        frames.append(frame)
    return frames


def test_insufficient_history_returns_cash():
    report = evaluate_market_state_router([_frame(index)[0] for index in range(10)])
    assert report["candidate_state"] == "CASH"
    assert report["decision_reason"] == "insufficient_contiguous_forward_history"
    assert report["authorizes_trading"] is False
    assert report["minimum_cash_weight"] == 1.0


def test_spot_led_continuation_qualifies_with_cash_reserve():
    prices = [100.0] * 163 + [100.0, 100.2, 100.4, 100.6, 101.0, 102.0]
    frames = _history(
        {"BTC": {"spot_flow": 0.35, "spot_book": 0.20, "perp_flow": 0.05, "basis_bps": 5.0}},
        series_prices={"BTC": prices},
    )
    report = evaluate_market_state_router(frames)
    assert report["candidate_state"] == "RESEARCH_CANDIDATES"
    assert report["selected_candidates"][0]["asset"] == "BTC"
    assert report["selected_candidates"][0]["sleeve"] == "spot_led_continuation"
    assert report["target_weights"] == {"BTC": 0.25}
    assert report["minimum_cash_weight"] == 0.75


def test_capitulation_recovery_proxy_qualifies():
    prices = [100.0] * 169
    prices[167] = 95.0
    prices[168] = 96.0
    frames = _history(
        {"BTC": {"open_interest": 90.0, "spot_flow": 0.30, "spot_book": 0.15, "basis_bps": -7.0}},
        series_prices={"BTC": prices},
        indexed_changes={167: {"BTC": {"basis_bps": -10.0}}},
    )
    report = evaluate_market_state_router(frames)
    selected = report["selected_candidates"][0]
    assert selected["asset"] == "BTC"
    assert selected["sleeve"] == "capitulation_recovery_proxy"
    assert report["asset_diagnostics"]["BTC"]["sleeves"]["capitulation_recovery_proxy"]["qualified"] is True


def test_negative_basis_normalization_qualifies():
    prices = [100.0] * 169
    prices[167] = 99.0
    prices[168] = 100.0
    frames = _history(
        {"BTC": {"open_interest": 95.0, "spot_flow": 0.20, "basis_bps": -20.0, "funding": -0.00001}},
        series_prices={"BTC": prices},
        indexed_changes={167: {"BTC": {"basis_bps": -30.0}}},
    )
    report = evaluate_market_state_router(frames)
    selected = report["selected_candidates"][0]
    assert selected["sleeve"] == "negative_basis_normalization"
    assert report["minimum_cash_weight"] == 0.75


def test_fresh_high_vix_blocks_all_candidates():
    prices = [100.0] * 163 + [100.0, 100.2, 100.4, 100.6, 101.0, 102.0]
    frames = _history(
        {"BTC": {"spot_flow": 0.35, "spot_book": 0.20, "perp_flow": 0.05}},
        series_prices={"BTC": prices},
        global_current={"vix": 40.0},
    )
    report = evaluate_market_state_router(frames)
    assert report["candidate_state"] == "CASH"
    assert report["decision_reason"] == "macro_risk_block"
    assert report["global_controls"]["total_exposure_cap"] == 0.0


def test_stale_stablecoin_data_enforces_conservative_cap():
    current, _ = _frame(168, global_changes={"stable_staleness": 20})
    week, _ = _frame(0)
    controls = _global_controls(current, week)
    assert controls["macro_available"] is True
    assert controls["stablecoin_available"] is False
    assert controls["total_exposure_cap"] == 0.25


def test_highly_correlated_candidates_reduce_to_one_asset():
    prices = [100.0 + index * 0.01 for index in range(163)] + [101.63, 101.8, 102.0, 102.3, 102.7, 103.7]
    frames = _history(
        {
            "BTC": {"spot_flow": 0.35, "spot_book": 0.20, "perp_flow": 0.05},
            "ETH": {"spot_flow": 0.35, "spot_book": 0.20, "perp_flow": 0.05},
        },
        series_prices={"BTC": prices, "ETH": prices},
    )
    report = evaluate_market_state_router(frames)
    assert len(report["qualified_candidates"]) == 2
    assert len(report["selected_candidates"]) == 1
    assert report["selected_candidates"][0]["asset"] == "BTC"
    assert report["correlation_filter"]["correlation_7d"] == pytest.approx(1.0)
    assert report["correlation_filter"]["removed_second_asset"] is True


def test_later_snapshots_do_not_change_prior_as_of_decision():
    prices = [100.0] * 163 + [100.0, 100.2, 100.4, 100.6, 101.0, 102.0]
    frames = _history(
        {"BTC": {"spot_flow": 0.35, "spot_book": 0.20, "perp_flow": 0.05}},
        series_prices={"BTC": prices},
    )
    prior = evaluate_market_state_router(frames)
    future, _ = _frame(169, asset_changes={"BTC": {"price": 1_000.0, "spot_flow": -1.0}})
    repeated = evaluate_market_state_router(frames + [future], as_of=frames[-1].hour)
    assert repeated == prior


def test_loader_uses_earliest_capture_in_hour(tmp_path):
    later_frame, later_payload = _frame(0, captured_minute=17)
    early_frame, early_payload = _frame(0, captured_minute=5)
    (tmp_path / "later.json").write_text(json.dumps(later_payload), encoding="utf-8")
    (tmp_path / "early.json").write_text(json.dumps(early_payload), encoding="utf-8")
    loaded = load_forward_snapshots(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].snapshot_id == early_frame.snapshot_id
    assert loaded[0].snapshot_id != later_frame.snapshot_id


def test_report_hash_and_safety_flags_are_deterministic():
    frames = _history()
    report = evaluate_market_state_router(frames)
    expected = report.pop("report_sha256")
    actual = hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
    assert actual == expected
    assert report["paper_only"] is True
    assert report["authorizes_trading"] is False
    assert report["minimum_cash_weight"] == 1.0
