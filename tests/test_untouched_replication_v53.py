from __future__ import annotations

from dataclasses import asdict

import numpy as np
import pytest

from tradebot.research import untouched_replication_v53 as v53


def portfolio(
    net_return: float,
    *,
    drawdown: float = 0.01,
    actions: int = 10,
    attenuated: int = 4,
) -> dict[str, object]:
    return {
        "net_return": net_return,
        "maximum_drawdown": drawdown,
        "target_changing_actions": actions,
        "attenuated_decision_count": attenuated,
        "never_added_asset": True,
        "never_increased_target": True,
    }


def window(name: str, excess: float) -> dict[str, object]:
    baseline = portfolio(0.01, attenuated=0)
    candidate = portfolio(0.01 + excess)
    return {
        "name": name,
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


def passing_result() -> dict[str, object]:
    continuous = window("continuous", 0.002)
    quarters = v53.summarize_windows([
        window("q1", 0.001),
        window("q2", 0.002),
        window("q3", -0.0005),
    ])
    sealed = v53.summarize_windows([
        window("s1", 0.001),
        window("s2", 0.001),
        window("s3", 0.001),
        window("s4", -0.0005),
        window("s5", 0.0),
    ])
    return {
        "continuous": continuous,
        "quarters": quarters,
        "sealed_windows": sealed,
        "delay_1_continuous": window("delay", -0.0005),
    }


def test_primary_specification_is_frozen() -> None:
    assert asdict(v53.PRIMARY) == {
        "family": "trend_state",
        "source": "mean:spot_return_7",
        "transform": "acceleration",
        "history": 90,
        "lag": 10,
        "event": "cross_up",
        "threshold": 0.3,
        "persistence": 7,
        "multiplier": 0.75,
    }


def test_secondary_specification_is_frozen() -> None:
    assert v53.SECONDARY.source == "positive_breadth:sma_distance_50"
    assert v53.SECONDARY.transform == "delta"
    assert v53.SECONDARY.event == "cross_down"
    assert v53.SECONDARY.multiplier == 0.75


def test_untouched_dates_are_exact() -> None:
    assert v53.START.isoformat().startswith("2025-10-01")
    assert v53.END.isoformat().startswith("2026-06-30")
    assert [name for name, _, _ in v53.QUARTERS] == [
        "2025-Q4", "2026-Q1", "2026-Q2"
    ]


def test_shift_activity_moves_forward_and_inserts_false() -> None:
    active = np.asarray([True, False, True, True], dtype=bool)
    assert v53.shift_activity(active).tolist() == [
        False, True, False, True
    ]
    assert v53.shift_activity(active, 2).tolist() == [
        False, False, True, False
    ]


def test_probability_map_uses_zero_for_active() -> None:
    class Fold:
        validation_dates = [1, 2, 3]

    values = v53.probabilities_from_activity(
        Fold(), np.asarray([True, False, True])
    )
    assert values == {1: 0.0, 2: 1.0, 3: 0.0}


def test_probability_map_rejects_length_mismatch() -> None:
    class Fold:
        validation_dates = [1, 2]

    with pytest.raises(v53.UntouchedReplicationV53Error):
        v53.probabilities_from_activity(
            Fold(), np.asarray([True])
        )


def test_relative_compounded_excess() -> None:
    values = [window("a", 0.01), window("b", 0.02)]
    result = v53.relative_compounded_excess(values, "standard")
    candidate = (1.02 * 1.03)
    baseline = (1.01 * 1.01)
    assert result == pytest.approx(candidate / baseline - 1.0)


def test_summarize_windows_counts_and_floors() -> None:
    summary = v53.summarize_windows([
        window("a", 0.001),
        window("b", -0.002),
        window("c", 0.0),
    ])
    assert summary["positive_standard_count"] == 1
    assert summary["minimum_standard_excess"] == -0.002
    assert len(summary["standard_excess_returns"]) == 3


def test_replication_gates_pass_for_robust_result() -> None:
    gates = v53.replication_gates(passing_result())
    assert gates
    assert all(gates.values())


def test_replication_gates_fail_negative_continuous() -> None:
    result = passing_result()
    result["continuous"] = window("continuous", -0.0001)
    gates = v53.replication_gates(result)
    assert not gates["continuous_standard_excess_positive"]
    assert not gates["continuous_stress_excess_positive"]


def test_replication_gates_fail_delay_floor() -> None:
    result = passing_result()
    result["delay_1_continuous"] = window("delay", -0.0011)
    gates = v53.replication_gates(result)
    assert not gates["one_day_delay_floor"]


def v52_report() -> dict[str, object]:
    return {
        "schema_version": "5.2-adversarial-alpha-funnel",
        "report_sha256": v53.V52_REPORT_SHA256,
        "sealed_evaluation_performed": False,
        "shortlist": [
            {"hypothesis": asdict(v53.PRIMARY)},
            {"hypothesis": asdict(v53.SECONDARY)},
        ],
    }


def test_validate_v52_report_accepts_exact_freeze() -> None:
    v53.validate_v52_report(v52_report())


def test_validate_v52_report_rejects_primary_change() -> None:
    report = v52_report()
    report["shortlist"][0]["hypothesis"]["threshold"] = 0.4
    with pytest.raises(v53.UntouchedReplicationV53Error):
        v53.validate_v52_report(report)


def test_replication_gates_fail_quarter_floor() -> None:
    result = passing_result()
    result["quarters"] = v53.summarize_windows([
        window("q1", 0.002),
        window("q2", 0.001),
        window("q3", -0.0026),
    ])
    gates = v53.replication_gates(result)
    assert not gates["quarter_loss_floor"]


def test_mechanism_summary_adds_single_pass_decision() -> None:
    summary = v53.mechanism_summary(passing_result())
    assert summary["untouched_replication_passed"] is True
    assert all(summary["replication_gates"].values())
