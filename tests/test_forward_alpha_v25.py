from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import tradebot.research.forward_alpha_v25 as v25
from tradebot.research.forward_alpha_v25 import evaluate_forward_alpha_v25
from tradebot.research.market_state_router import ASSETS, SnapshotFrame


def _asset_record(
    *,
    mid: float = 100.0,
    basis_bps: float = 5.0,
    spread_bps: float = 5.0,
    bid_notional: float = 1_000.0,
    ask_notional: float = 1_000.0,
    spot_flow: float = 0.0,
    book: float = 0.0,
    perp_flow: float = 0.0,
    funding: float = 0.0,
    open_interest: float = 100.0,
) -> dict[str, object]:
    return {
        "spot_quote": {"available": True, "mid": mid},
        "spot_book": {
            "available": True,
            "spread_bps": spread_bps,
            "bid_notional": bid_notional,
            "ask_notional": ask_notional,
            "imbalance": book,
        },
        "spot_trade_flow": {"available": True, "taker_imbalance": spot_flow},
        "perp_state": {
            "available": True,
            "funding": funding,
            "open_interest_base": open_interest,
        },
        "perp_trade_flow": {"available": True, "reported_side_imbalance": perp_flow},
        "cross_venue": {"available": True, "spot_perp_basis_bps": basis_bps},
    }


def _frames(mutator=None, count: int = 169) -> list[SnapshotFrame]:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    frames: list[SnapshotFrame] = []
    for index in range(count):
        assets = {asset: _asset_record() for asset in ASSETS}
        if mutator is not None:
            mutator(index, assets)
        hour = start + timedelta(hours=index)
        frames.append(SnapshotFrame(
            hour=hour,
            captured_at=hour + timedelta(minutes=1),
            snapshot_id=f"snapshot-{index:04d}",
            record_sha256=f"{index:064x}",
            assets=assets,
            global_state={},
            source_path=f"snapshot-{index:04d}.json",
        ))
    return frames


def test_insufficient_history_fails_closed_without_performance() -> None:
    report = evaluate_forward_alpha_v25(_frames(count=20))

    assert report["candidate_state"] == "CASH"
    assert report["decision_reason"] == "insufficient_contiguous_forward_history"
    assert report["paper_only"] is True
    assert report["authorizes_trading"] is False
    assert report["authorizes_shadow_paper"] is False
    assert report["minimum_cash_weight"] == 1.0
    assert report["missing_required_hours"]
    assert not any(key in report for key in ("return", "pnl", "profit", "drawdown", "sharpe"))


def test_residual_momentum_microstructure_family() -> None:
    eth_prices = {
        162: 100.0,
        163: 100.2,
        164: 100.4,
        165: 100.6,
        166: 100.8,
        167: 101.0,
        168: 102.0,
    }

    def mutate(index: int, assets: dict[str, dict[str, object]]) -> None:
        if index >= 162:
            assets["ETH"] = _asset_record(
                mid=eth_prices[index],
                basis_bps=4.0,
                spot_flow=0.20 if index == 168 else 0.0,
                book=0.08 if index == 168 else 0.0,
                perp_flow=0.02 if index == 168 else 0.0,
                funding=0.00002,
                open_interest=102.0 if index == 168 else 100.0,
            )

    report = evaluate_forward_alpha_v25(_frames(mutate))

    selected = report["selected_candidates"]
    assert selected
    assert selected[0]["asset"] == "ETH"
    assert selected[0]["family"] == "residual_momentum_microstructure"
    assert selected[0]["amplitude"] >= 0.008
    assert report["target_weights"] == {"ETH": 0.15}
    assert report["minimum_cash_weight"] == 0.85


def test_funding_basis_state_transition_family() -> None:
    def mutate(index: int, assets: dict[str, dict[str, object]]) -> None:
        if index == 162:
            assets["BTC"] = _asset_record(
                mid=99.0,
                basis_bps=-80.0,
                funding=-0.00050,
                open_interest=100.0,
            )
        elif index == 167:
            assets["BTC"] = _asset_record(
                mid=99.0,
                basis_bps=-20.0,
                funding=-0.00030,
                open_interest=100.0,
            )
        elif index == 168:
            assets["BTC"] = _asset_record(
                mid=100.0,
                basis_bps=-10.0,
                spot_flow=0.18,
                book=0.08,
                perp_flow=0.02,
                funding=-0.00020,
                open_interest=95.0,
            )

    report = evaluate_forward_alpha_v25(_frames(mutate))

    selected = report["selected_candidates"]
    assert selected
    assert selected[0]["asset"] == "BTC"
    assert selected[0]["family"] == "funding_basis_state_transition"
    assert selected[0]["amplitude"] >= 0.006
    features = report["asset_diagnostics"]["BTC"]["features"]
    assert features["open_interest_change_6h"] <= -0.03
    assert features["basis_change_1h_bps"] >= 3.0


def test_sweep_replenishment_continuation_family() -> None:
    prices = {165: 100.0, 166: 100.2, 167: 100.3, 168: 101.0}

    def mutate(index: int, assets: dict[str, dict[str, object]]) -> None:
        if index in prices:
            if index == 167:
                assets["SOL"] = _asset_record(
                    mid=prices[index],
                    spread_bps=30.0,
                    bid_notional=500.0,
                    ask_notional=500.0,
                )
            elif index == 168:
                assets["SOL"] = _asset_record(
                    mid=prices[index],
                    spread_bps=10.0,
                    bid_notional=800.0,
                    ask_notional=800.0,
                    spot_flow=0.24,
                    book=0.10,
                    perp_flow=0.04,
                    funding=0.00002,
                    open_interest=104.0,
                )
            else:
                assets["SOL"] = _asset_record(mid=prices[index])

    report = evaluate_forward_alpha_v25(_frames(mutate))

    selected = report["selected_candidates"]
    assert selected
    assert selected[0]["asset"] == "SOL"
    assert selected[0]["family"] == "sweep_replenishment_continuation"
    features = report["asset_diagnostics"]["SOL"]["features"]
    assert features["spot_book_notional_change_1h"] >= 0.25
    assert features["spread_contraction_fraction"] >= 0.20


def test_weak_event_fails_edge_to_cost_hurdle() -> None:
    def mutate(index: int, assets: dict[str, dict[str, object]]) -> None:
        if index == 168:
            assets["ETH"] = _asset_record(
                mid=100.5,
                spot_flow=0.20,
                book=0.08,
                perp_flow=0.02,
                funding=0.0,
            )

    report = evaluate_forward_alpha_v25(_frames(mutate))

    assert report["candidate_state"] == "CASH"
    assert report["target_weights"] == {}
    assert report["minimum_cash_weight"] == 1.0


def test_report_is_deterministic_bounded_and_safe() -> None:
    frames = _frames()
    first = evaluate_forward_alpha_v25(frames)
    second = evaluate_forward_alpha_v25(list(reversed(frames)))

    assert first == second
    assert first["paper_only"] is True
    assert first["authorizes_trading"] is False
    assert first["authorizes_shadow_paper"] is False
    assert first["minimum_cash_weight"] >= 0.70
    assert sum(first["target_weights"].values()) <= 0.30
    assert all(weight <= 0.15 for weight in first["target_weights"].values())
    assert first["report_sha256"]


def test_beta_uses_all_168_completed_hourly_returns() -> None:
    observed: list[tuple[int, int]] = []
    original = v25._beta

    def capture(asset_returns: list[float], btc_returns: list[float]) -> float:
        observed.append((len(asset_returns), len(btc_returns)))
        return original(asset_returns, btc_returns)

    with patch.object(v25, "_beta", side_effect=capture):
        features = v25._feature_set(_frames(), "ETH")

    assert features is not None
    assert observed == [(168, 168)]
