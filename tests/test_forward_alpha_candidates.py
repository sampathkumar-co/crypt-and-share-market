from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradebot.research.forward_alpha_candidates import evaluate_forward_alpha
from tradebot.research.market_state_router import ASSETS, SnapshotFrame


def _asset_record(
    *,
    mid: float = 100.0,
    basis_bps: float = 5.0,
    spot_flow: float = 0.0,
    book: float = 0.0,
    perp_flow: float = 0.0,
    funding: float = 0.0,
    open_interest: float = 100.0,
) -> dict[str, object]:
    return {
        "spot_quote": {"available": True, "mid": mid},
        "spot_book": {"available": True, "imbalance": book},
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
    report = evaluate_forward_alpha(_frames(count=20))

    assert report["candidate_state"] == "CASH"
    assert report["decision_reason"] == "insufficient_contiguous_forward_history"
    assert report["paper_only"] is True
    assert report["authorizes_trading"] is False
    assert report["minimum_cash_weight"] == 1.0
    assert report["missing_required_hours"]
    assert not any(key in report for key in ("return", "pnl", "drawdown", "sharpe"))


def test_cross_venue_dislocation_candidate_is_selected_with_cash_cap() -> None:
    def mutate(index: int, assets: dict[str, dict[str, object]]) -> None:
        if index == 167:
            assets["BTC"] = _asset_record(basis_bps=-30.0, open_interest=100.0)
        elif index == 168:
            assets["BTC"] = _asset_record(
                basis_bps=-20.0,
                spot_flow=0.20,
                book=0.10,
                perp_flow=0.05,
                funding=0.0,
                open_interest=90.0,
            )

    report = evaluate_forward_alpha(_frames(mutate))

    assert report["candidate_state"] == "RESEARCH_CANDIDATES"
    assert report["selected_candidates"][0]["asset"] == "BTC"
    assert report["selected_candidates"][0]["family"] == "cross_venue_dislocation_normalization"
    assert report["target_weights"] == {"BTC": 0.20}
    assert report["minimum_cash_weight"] == 0.80
    assert report["authorizes_trading"] is False


def test_liquidity_vacuum_recovery_requires_completed_reversal() -> None:
    prices = {
        162: 100.0,
        163: 98.0,
        164: 96.0,
        165: 94.0,
        166: 92.0,
        167: 90.0,
        168: 91.0,
    }

    def mutate(index: int, assets: dict[str, dict[str, object]]) -> None:
        if index >= 162:
            assets["BTC"] = _asset_record(
                mid=prices[index],
                basis_bps=-8.0,
                spot_flow=0.18 if index == 168 else 0.0,
                book=0.08 if index == 168 else 0.0,
                perp_flow=0.02 if index == 168 else 0.0,
                funding=-0.00002,
                open_interest=88.0 if index == 168 else 100.0,
            )

    report = evaluate_forward_alpha(_frames(mutate))

    selected = report["selected_candidates"]
    assert selected
    assert selected[0]["asset"] == "BTC"
    assert selected[0]["family"] == "liquidity_vacuum_recovery"
    features = report["asset_diagnostics"]["BTC"]["features"]
    assert features["spot_return_1h"] > 0.0
    assert features["spot_return_6h"] < 0.0
    assert features["open_interest_change_6h"] < 0.0


def test_spot_led_flow_persistence_uses_completed_multi_hour_evidence() -> None:
    prices = {162: 100.0, 163: 100.2, 164: 100.4, 165: 101.0, 166: 101.4, 167: 102.0, 168: 103.0}

    def mutate(index: int, assets: dict[str, dict[str, object]]) -> None:
        if index >= 162:
            assets["BTC"] = _asset_record(
                mid=prices[index],
                basis_bps=2.0,
                spot_flow=0.22 if index >= 166 else 0.0,
                book=0.08 if index == 168 else 0.0,
                perp_flow=0.04 if index == 168 else 0.0,
                funding=0.00002,
                open_interest=104.0 if index == 168 else 100.0,
            )

    report = evaluate_forward_alpha(_frames(mutate))

    selected = report["selected_candidates"]
    assert selected
    assert selected[0]["asset"] == "BTC"
    assert selected[0]["family"] == "spot_led_flow_persistence"
    features = report["asset_diagnostics"]["BTC"]["features"]
    assert features["positive_spot_flow_hours_3h"] == 3.0
    assert features["spot_return_3h"] > 0.0
    assert features["spot_return_6h"] > 0.0


def test_report_is_deterministic_and_never_authorizes_trading() -> None:
    frames = _frames()
    first = evaluate_forward_alpha(frames)
    second = evaluate_forward_alpha(list(reversed(frames)))

    assert first == second
    assert first["paper_only"] is True
    assert first["authorizes_trading"] is False
    assert first["minimum_cash_weight"] >= 0.60
    assert first["report_sha256"]
