from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from tradebot.research import adversarial_alpha_funnel_v52 as v52


def stamp(day: int) -> datetime:
    return datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)


def hypothesis(**changes: object) -> v52.Hypothesis:
    payload = {
        "family": "trend_state",
        "source": "market_return_7",
        "transform": "level",
        "history": 20,
        "lag": 1,
        "event": "low",
        "threshold": 0.20,
        "persistence": 1,
        "multiplier": 0.50,
    }
    payload.update(changes)
    return v52.Hypothesis(**payload)


def fold_data() -> v52.FoldData:
    dates = [stamp(index) for index in range(8)]
    return v52.FoldData(
        name="WF-X",
        panel_dates=dates,
        validation_dates=dates,
        validation_positions=np.arange(len(dates)),
        panel={"market_return_7": np.arange(8, dtype=float)},
        baseline={
            "daily_returns": [0.0, -0.01, -0.01, 0.01, 0.01, -0.01, 0.0, 0.0],
            "net_return": 0.0,
            "target_changing_actions": 3,
        },
        baseline_daily_returns=np.asarray([
            0.0, -0.01, -0.01, 0.01, 0.01, -0.01, 0.0, 0.0
        ]),
        risky_daily_returns=np.asarray([
            0.0, -0.01, -0.01, 0.01, 0.01, -0.01, 0.0, 0.0
        ]),
        cash_daily_returns=np.zeros(8),
        rebalance_mask=np.asarray([
            True, False, False, True, False, False, True, False
        ]),
        selected_rebalance_mask=np.asarray([
            True, False, False, True, False, False, False, False
        ]),
        bundle=None,  # type: ignore[arg-type]
        predictions={},
        validation_mask=np.ones(8, dtype=bool),
    )


def test_canonical_hypothesis_is_stable() -> None:
    value = hypothesis()
    assert v52.canonical_hypothesis(value) == v52.canonical_hypothesis(value)
    assert '"source":"market_return_7"' in v52.canonical_hypothesis(value)


def test_delta_and_acceleration_use_only_past_values() -> None:
    values = np.asarray([1.0, 2.0, 4.0, 7.0, 11.0])
    delta = v52.transformed_series(values, "delta", 1)
    acceleration = v52.transformed_series(values, "acceleration", 1)
    assert np.isnan(delta[0])
    assert np.allclose(delta[1:], [1.0, 2.0, 3.0, 4.0])
    assert np.isnan(acceleration[0])
    assert np.isnan(acceleration[1])
    assert np.allclose(acceleration[2:], [1.0, 1.0, 1.0])


def test_rolling_percentile_excludes_current_value() -> None:
    values = np.asarray([1.0, 2.0, 3.0, 0.0])
    ranked = v52.rolling_percentile(values, 2)
    assert np.isnan(ranked[0]) and np.isnan(ranked[1])
    assert ranked[2] == 1.0
    assert ranked[3] == 0.0


def test_persist_events_extends_forward_only() -> None:
    events = np.asarray([False, True, False, False, True, False])
    active = v52.persist_events(events, 3)
    assert active.tolist() == [False, True, True, True, True, True]


def test_exposure_multiplier_persists_until_next_rebalance() -> None:
    fold = fold_data()
    active = np.asarray([True, False, False, False, False, False, False, False])
    exposure = v52.exposure_path(fold, active, 0.5)
    assert exposure.tolist() == [0.5, 0.5, 0.5, 1.0, 1.0, 1.0, 0.0, 0.0]


def test_proxy_fold_rewards_attenuating_negative_risky_days() -> None:
    fold = fold_data()
    active = np.asarray([True, False, False, False, False, False, False, False])
    report = v52.proxy_fold(fold, active, 0.5)
    assert report["interventions"] == 1
    assert report["proxy_excess"] > 0.0


def test_fingerprint_changes_with_multiplier() -> None:
    fold = fold_data()
    active = np.asarray([True, False, False, False, False, False, False, False])
    left = v52.fingerprint_for([fold], [active], 0.5)
    right = v52.fingerprint_for([fold], [active], 0.75)
    assert left != right
    assert left == v52.fingerprint_for([fold], [active], 0.5)


def test_source_family_classification() -> None:
    assert v52.source_family("model_selected_utility") == "model_confidence"
    assert v52.source_family("mean:funding_z_30") == "positioning_flow"
    assert v52.source_family("std:volatility_30") == "volatility_structure"
    assert v52.source_family("breadth_100") == "breadth_leadership"
    assert v52.source_family("market_return_30") == "trend_state"


def test_nearest_grid_values_clamp_at_edges() -> None:
    assert v52.nearest_threshold(0.10, -1) == 0.10
    assert v52.nearest_threshold(0.90, 1) == 0.90
    assert v52.nearest_history(20, -1) == 20
    assert v52.nearest_history(180, 1) == 180


def test_activity_uses_validation_positions_and_no_future() -> None:
    fold = fold_data()
    value = hypothesis(history=2, event="high", threshold=0.90)
    active = v52.activity_for_fold(fold, value, {})
    assert active[:2].tolist() == [False, False]
    assert active[2:].tolist() == [True, True, True, True, True, True]


def test_generator_is_deterministic_and_exact(monkeypatch) -> None:
    monkeypatch.setattr(v52, "RAW_HYPOTHESIS_COUNT", 600)
    families = {
        "trend_state": ["market_return_7"],
        "breadth_leadership": ["breadth_100"],
        "positioning_flow": ["mean:funding_z_30"],
        "volatility_structure": ["std:volatility_30"],
        "model_confidence": ["model_selected_utility"],
        "relative_reversal": ["market_return_30"],
    }
    first = v52.generate_hypotheses(families)
    second = v52.generate_hypotheses(families)
    assert len(first) == 600
    assert [v52.canonical_hypothesis(value) for value in first] == [
        v52.canonical_hypothesis(value) for value in second
    ]
    assert {value.family for value in first} == set(families)


def test_proxy_eligibility_requires_robust_fold_pattern() -> None:
    report = {
        "intervention_count": 12,
        "intervention_coverage": 0.2,
        "positive_fold_count": 4,
        "compounded_excess": 0.01,
        "minimum_fold_excess": -0.001,
        "best_fold_concentration": 0.5,
    }
    assert v52.proxy_eligible(report)
    assert not v52.proxy_eligible({**report, "positive_fold_count": 3})
    assert not v52.proxy_eligible({**report, "compounded_excess": -0.01})


def candidate(family: str, source: str) -> v52.Candidate:
    value = hypothesis(family=family, source=source)
    return v52.Candidate(
        hypothesis=value,
        fingerprint=source,
        proxy={
            "minimum_fold_excess": 0.0,
            "positive_fold_count": 6,
            "compounded_excess": 0.01,
            "best_fold_concentration": 0.3,
            "intervention_coverage": 0.2,
        },
    )


def test_shortlist_keeps_distinct_families() -> None:
    values = [
        candidate("trend_state", "a"),
        candidate("trend_state", "b"),
        candidate("positioning_flow", "c"),
        candidate("model_confidence", "d"),
        candidate("volatility_structure", "e"),
    ]
    selected = v52.choose_shortlist(values)
    assert len(selected) == 3
    assert [value.hypothesis.family for value in selected] == [
        "trend_state", "positioning_flow", "model_confidence"
    ]


def test_manifest_is_deterministic_jsonl() -> None:
    values = [hypothesis(source="a"), hypothesis(source="b")]
    first = v52.manifest_bytes(values)
    second = v52.manifest_bytes(values)
    assert first == second
    assert first.endswith(b"\n")
    assert first.count(b"\n") == 2


def test_source_inventory_includes_relative_reversal_all_sources() -> None:
    panel = {
        "market_return_7": np.zeros(2),
        "breadth_100": np.zeros(2),
        "mean:funding_z_30": np.zeros(2),
        "std:volatility_30": np.zeros(2),
        "model_selected_utility": np.zeros(2),
    }
    inventory = v52.source_inventory(panel)
    assert set(inventory["relative_reversal"]) == set(panel)
    assert inventory["model_confidence"] == ["model_selected_utility"]
