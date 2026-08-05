from __future__ import annotations

from tradebot.research import consensus_shrunk_ensemble_v62 as v62


def _targets(active: int, weight: float) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for index, model in enumerate(v62.MEMBERS):
        result[model.model_id] = {"BTC": weight} if index < active else {}
    return result


def test_full_agreement_reproduces_mean_target():
    target = v62._consensus_target(_targets(16, 0.10))
    assert target == {"BTC": 0.10}


def test_partial_agreement_shrinks_without_parameter():
    target = v62._consensus_target(_targets(8, 0.10))
    # Mean is 5%; agreement is 50%; consensus exposure is 2.5%.
    assert abs(target["BTC"] - 0.025) < 1e-15


def test_consensus_never_exceeds_plain_mean():
    member_targets = _targets(11, 0.08)
    consensus = v62._consensus_target(member_targets)["BTC"]
    plain_mean = 11 * 0.08 / 16
    assert 0.0 < consensus <= plain_mean


def test_missing_member_fails_closed():
    import pytest

    member_targets = _targets(16, 0.10)
    member_targets.pop(next(iter(member_targets)))
    with pytest.raises(v62.ParameterNeighborhoodV61Error):
        v62._consensus_target(member_targets)
