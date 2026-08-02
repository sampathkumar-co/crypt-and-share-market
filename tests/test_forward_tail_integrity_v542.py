from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tradebot.research import forward_tail_integrity_v542 as v542


def synthetic_states(count: int = 230):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    dates = [start + timedelta(days=index) for index in range(count)]
    states = {asset: {} for asset in v542.ASSETS}
    for asset_index, asset in enumerate(v542.ASSETS):
        for day_index, stamp in enumerate(dates):
            base = 100.0 + 20.0 * asset_index + 0.35 * day_index
            spot = v542.v54.sources.DailyBar(
                stamp,
                base,
                base * 1.012,
                base * 0.988,
                base * 1.003,
                1000.0 + 3.0 * day_index + asset_index,
                510.0 + day_index + asset_index,
            )
            perp = v542.v54.sources.DailyBar(
                stamp,
                base * 1.001,
                base * 1.014,
                base * 0.989,
                base * 1.004,
                1200.0 + 4.0 * day_index + asset_index,
                600.0 + day_index + asset_index,
            )
            states[asset][stamp] = v542.v54.sources.DailyAssetState(
                day=stamp,
                spot=spot,
                perp=perp,
                funding=0.00001 * (asset_index + 1),
                open_interest=10000.0 + 10.0 * day_index + 50 * asset_index,
                basis=perp.close / spot.close - 1.0,
                spot_flow=0.02 * (asset_index - 2),
                perp_flow=0.015 * (asset_index - 2),
            )
    return dates, states
def next_opens(states, stamp):
    return {
        asset: float(states[asset][stamp].spot.open * 1.01)
        for asset in v542.ASSETS
    }


def portfolio(
    net_return: float,
    *,
    drawdown: float = 0.01,
    actions: int = 5,
    attenuated: int = 2,
):
    return {
        "net_return": net_return,
        "maximum_drawdown": drawdown,
        "target_changing_actions": actions,
        "attenuated_decision_count": attenuated,
        "never_added_asset": True,
        "never_increased_target": True,
    }


def simulation(excess: float = 0.001):
    baseline = portfolio(0.01, attenuated=0)
    candidate = portfolio(0.01 + excess)
    return {
        "standard": {
            "baseline": baseline,
            "candidate": candidate,
            "excess_return": excess,
        },
        "stress": {
            "baseline": baseline,
            "candidate": candidate,
            "excess_return": excess,
        },
    }


def exact_overlap():
    return {
        "exact": True,
        "feature_names_exact": True,
        "features_exact": True,
        "return1_exact": True,
        "shared_order_exact": True,
    }


def complete_august_report():
    return {
        "successful_archive_count": len(v542.ASSETS),
        "missing_count": 0,
    }
def test_august_spot_url_is_frozen() -> None:
    url = v542.august_spot_url("BTC")
    assert url.endswith("BTCUSDT-1d-2026-08-01.zip")
    assert "/spot/daily/klines/" in url


def test_validate_v541_report_accepts_partial_smoke() -> None:
    report = {
        "schema_version": v542.v54.SCHEMA_VERSION,
        "report_sha256": v542.V541_REPORT_SHA256,
        "status": "FORWARD_SMOKE_PASSED",
        "simulation": {
            "standard": {"candidate": {"decision_count": 23}}
        },
    }
    v542.validate_v541_report(report)


def test_validate_v541_report_rejects_wrong_count() -> None:
    report = {
        "schema_version": v542.v54.SCHEMA_VERSION,
        "report_sha256": v542.V541_REPORT_SHA256,
        "status": "FORWARD_SMOKE_PASSED",
        "simulation": {
            "standard": {"candidate": {"decision_count": 31}}
        },
    }
    with pytest.raises(v542.ForwardTailIntegrityV542Error):
        v542.validate_v541_report(report)


def test_tail_builder_exactly_matches_generic_overlap() -> None:
    dates, states = synthetic_states()
    generic = v542.v54.build_dataset(states)
    tail = v542.build_tail_dataset(
        states,
        next_opens(states, dates[-1]),
        evaluation_end=dates[-2],
    )
    report = v542.overlap_integrity(generic, tail)
    assert report["exact"] is True
    assert report["generic_rows_missing_from_tail"] == 0
    assert report["tail_row_count"] > report["generic_row_count"]


def test_tail_last_return_uses_entry_then_external_exit_open() -> None:
    dates, states = synthetic_states()
    opens = next_opens(states, dates[-1])
    tail = v542.build_tail_dataset(
        states,
        opens,
        evaluation_end=dates[-2],
    )
    key = (dates[-2], "BTC")
    index = list(zip(tail.dates, tail.assets, strict=True)).index(key)
    entry = states["BTC"][dates[-1]].spot.open
    assert tail.return1[index] == pytest.approx(opens["BTC"] / entry - 1.0)
def test_tail_builder_rejects_missing_next_open_asset() -> None:
    dates, states = synthetic_states()
    opens = next_opens(states, dates[-1])
    opens.pop("ADA")
    with pytest.raises(v542.ForwardTailIntegrityV542Error):
        v542.build_tail_dataset(
            states,
            opens,
            evaluation_end=dates[-2],
        )


def test_correction_gates_pass_safe_positive_result() -> None:
    dates = [v542.START + timedelta(days=index) for index in range(30)]
    gates = v542.correction_gates(
        simulation(),
        exact_overlap(),
        dates,
        complete_august_report(),
    )
    assert all(gates.values())


def test_correction_gate_rejects_inexact_overlap() -> None:
    dates = [v542.START + timedelta(days=index) for index in range(30)]
    overlap = exact_overlap()
    overlap["exact"] = False
    gates = v542.correction_gates(
        simulation(),
        overlap,
        dates,
        complete_august_report(),
    )
    assert gates["exact_generic_overlap"] is False


def test_correction_status_transitions() -> None:
    dates = [v542.START + timedelta(days=index) for index in range(30)]
    complete = complete_august_report()
    assert v542.correction_status(dates[:-1], complete, 1, None) == (
        "FORWARD_TAIL_DATA_INCONCLUSIVE"
    )
    assert v542.correction_status(dates, complete, 0, None) == (
        "FORWARD_TAIL_NO_SIGNAL"
    )
    assert v542.correction_status(dates, complete, 1, {"a": True}) == (
        "FORWARD_TAIL_PASSED"
    )
    assert v542.correction_status(
        dates, complete, 1, {"a": False}
    ) == "FORWARD_TAIL_FAILED"


def test_missing_august_archives_are_inconclusive(monkeypatch) -> None:
    monkeypatch.setattr(v542, "ASSETS", ("BTC",))

    def missing(url: str, *, optional_404: bool = False):
        assert optional_404 is True
        return None

    opens, report = v542.load_august_exit_opens(downloader=missing)
    assert opens == {}
    assert report["successful_archive_count"] == 0
    assert report["missing_count"] == 1
