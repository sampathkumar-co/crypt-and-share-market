from __future__ import annotations

import pytest

from tradebot.backtest.research_selection import (
    annualization_for_market,
    balanced_candidate_pairs,
    metrics_are_active,
    required_warmup_bars,
)
from tradebot.models import Market


def test_balanced_budget_covers_every_parameter_set_before_repeating() -> None:
    parameters = [{"lookback": value} for value in range(27)]
    profiles = [{"profile": value} for value in range(3)]

    pairs = balanced_candidate_pairs(parameters, profiles, 27)

    assert len(pairs) == 27
    assert {params["lookback"] for params, _ in pairs} == set(range(27))
    assert {profile["profile"] for _, profile in pairs} == {0, 1, 2}


def test_balanced_budget_rotates_profiles_without_duplicate_pairs() -> None:
    parameters = [{"lookback": value} for value in range(4)]
    profiles = [{"profile": value} for value in range(3)]

    pairs = balanced_candidate_pairs(parameters, profiles, 10)
    identities = [
        (params["lookback"], profile["profile"])
        for params, profile in pairs
    ]

    assert len(identities) == 10
    assert len(set(identities)) == 10
    assert set(identities[:4]) == {(0, 0), (1, 1), (2, 2), (3, 0)}


def test_balanced_budget_validates_and_caps_budget() -> None:
    parameters = [{"p": 1}, {"p": 2}]
    profiles = [{"e": 1}, {"e": 2}]

    assert len(balanced_candidate_pairs(parameters, profiles, 99)) == 4
    assert balanced_candidate_pairs([], profiles, 2) == []
    assert balanced_candidate_pairs(parameters, [], 2) == []
    with pytest.raises(ValueError, match="positive"):
        balanced_candidate_pairs(parameters, profiles, 0)


def test_required_warmup_includes_regime_history() -> None:
    assert required_warmup_bars(3) == 30
    assert required_warmup_bars(45) == 46
    assert required_warmup_bars(3, regime_lookback=0) == 10
    with pytest.raises(ValueError, match="negative"):
        required_warmup_bars(-1)


def test_active_period_requires_actual_unseen_trades() -> None:
    assert metrics_are_active({"trades": 1}) is True
    assert metrics_are_active({"trades": 0, "net_return": 0.1}) is False
    assert metrics_are_active({}) is False


def test_market_annualization_is_explicit() -> None:
    assert annualization_for_market(Market.CRYPTO) == 365
    assert annualization_for_market(Market.EQUITY) == 252
