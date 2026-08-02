from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from tradebot.research import july_forward_smoke_v54 as v54


def portfolio(
    net_return: float,
    *,
    drawdown: float = 0.01,
    actions: int = 5,
    attenuated: int = 2,
) -> dict[str, object]:
    return {
        "net_return": net_return,
        "maximum_drawdown": drawdown,
        "target_changing_actions": actions,
        "attenuated_decision_count": attenuated,
        "never_added_asset": True,
        "never_increased_target": True,
    }


def simulation(excess: float = 0.001) -> dict[str, object]:
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


def v52_report() -> dict[str, object]:
    return {
        "schema_version": "5.2-adversarial-alpha-funnel",
        "report_sha256": v54.V52_REPORT_SHA256,
        "sealed_evaluation_performed": False,
        "shortlist": [
            {"hypothesis": asdict(v54.CANDIDATE)},
            {"hypothesis": asdict(v54.v53.SECONDARY)},
        ],
    }


def v53_report() -> dict[str, object]:
    return {
        "schema_version": "5.3-untouched-nine-month-replication",
        "report_sha256": v54.V53_REPORT_SHA256,
        "status": "UNTOUCHED_MECHANISM_REPLICATION_FAILED",
        "primary": {"hypothesis": asdict(v54.CANDIDATE)},
    }


def test_july_dates_are_exact() -> None:
    dates = v54.july_dates()
    assert len(dates) == 31
    assert dates[0].date().isoformat() == "2026-07-01"
    assert dates[-1].date().isoformat() == "2026-07-31"


def test_candidate_is_primary_with_one_day_delay() -> None:
    assert v54.CANDIDATE == v54.v53.PRIMARY
    assert v54.CANDIDATE.source == "mean:spot_return_7"
    assert v54.CANDIDATE.multiplier == 0.75


def test_daily_urls_use_frozen_binance_paths() -> None:
    stamp = datetime(2026, 7, 4, tzinfo=timezone.utc)
    urls = v54.daily_urls("BTC", stamp)
    assert urls["spot"].endswith("BTCUSDT-1d-2026-07-04.zip")
    assert "/futures/um/daily/klines/" in urls["perp"]
    assert "funding" not in urls
    assert urls["metrics"].endswith("BTCUSDT-metrics-2026-07-04.zip")
    assert v54.monthly_funding_url("BTC").endswith(
        "BTCUSDT-fundingRate-2026-07.zip"
    )


def test_validate_prior_reports_accepts_exact_provenance() -> None:
    v54.validate_prior_reports(v52_report(), v53_report())


def test_validate_prior_reports_rejects_v53_hash() -> None:
    report = v53_report()
    report["report_sha256"] = "bad"
    with pytest.raises(v54.JulyForwardSmokeV54Error):
        v54.validate_prior_reports(v52_report(), report)


def test_smoke_gates_pass_for_positive_safe_result() -> None:
    gates = v54.smoke_gates(simulation(), 31)
    assert all(gates.values())


def test_smoke_gates_fail_negative_excess() -> None:
    gates = v54.smoke_gates(simulation(-0.001), 31)
    assert not gates["standard_excess_positive"]
    assert not gates["stress_excess_positive"]


def test_result_status_data_inconclusive() -> None:
    assert v54.result_status(28, 2, None) == (
        "FORWARD_SMOKE_DATA_INCONCLUSIVE"
    )


def test_result_status_no_signal() -> None:
    assert v54.result_status(31, 0, None) == "FORWARD_SMOKE_NO_SIGNAL"


def test_result_status_pass_and_fail() -> None:
    gates = {"a": True, "b": True}
    assert v54.result_status(31, 2, gates) == "FORWARD_SMOKE_PASSED"
    assert v54.result_status(31, 2, {"a": True, "b": False}) == (
        "FORWARD_SMOKE_FAILED"
    )


def test_common_only_filters_noncommon_dates(monkeypatch) -> None:
    monkeypatch.setattr(v54, "ASSETS", ("BTC", "ETH"))
    first = datetime(2026, 7, 1, tzinfo=timezone.utc)
    second = datetime(2026, 7, 2, tzinfo=timezone.utc)
    extension = {
        "BTC": {first: "a", second: "b"},
        "ETH": {first: "c"},
    }
    filtered = v54.common_only(extension, [first])
    assert filtered == {"BTC": {first: "a"}, "ETH": {first: "c"}}


def test_merge_states_rejects_overlap(monkeypatch) -> None:
    monkeypatch.setattr(v54, "ASSETS", ("BTC",))
    stamp = datetime(2026, 7, 1, tzinfo=timezone.utc)
    with pytest.raises(v54.JulyForwardSmokeV54Error):
        v54.merge_states({"BTC": {stamp: "old"}}, {"BTC": {stamp: "new"}})


def test_missing_archives_are_inconclusive_not_imputed(monkeypatch) -> None:
    stamp = datetime(2026, 7, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(v54, "ASSETS", ("BTC",))
    monkeypatch.setattr(v54, "july_dates", lambda: [stamp])

    def missing_downloader(url: str, *, optional_404: bool = False):
        assert optional_404 is True
        return None

    extension, report = v54.load_july_extension(
        max_workers=1,
        downloader=missing_downloader,
    )
    assert extension == {"BTC": {}}
    assert report["requested_archive_count"] == 4
    assert report["missing_component_count"] == 4
    assert report["common_complete_date_count"] == 0


def test_smoke_gate_detects_action_increase() -> None:
    value = simulation()
    value["standard"]["candidate"]["target_changing_actions"] = 6
    gates = v54.smoke_gates(value, 31)
    assert not gates["standard_actions_not_increased"]
