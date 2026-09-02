from __future__ import annotations

import pytest

from tradebot.research.active_strategy_registry_v60 import (
    assert_research_allowed,
    load_strategy_boundary,
)


def test_registry_is_paper_only_and_locked() -> None:
    boundary = load_strategy_boundary()
    assert boundary.paper_only is True
    assert boundary.authorizes_trading is False
    assert boundary.authorizes_continuous_paper is False


def test_champion_and_forward_program_are_active() -> None:
    boundary = load_strategy_boundary()
    assert "v3.1.2-v3.2-yield-trend" in boundary.active_ids
    assert "v3.3-forward-observation" in boundary.active_ids


def test_rejected_families_are_retired() -> None:
    boundary = load_strategy_boundary()
    assert "mean_reversion" in boundary.retired_families
    assert "capitulation_recovery_entries" in boundary.retired_families
    assert "short_horizon_intraday_momentum" in boundary.retired_families


def test_retired_family_cannot_be_reopened() -> None:
    with pytest.raises(ValueError, match="retired strategy family"):
        assert_research_allowed(
            strategy_id="v3.1.2-v3.2-yield-trend",
            family="mean_reversion",
        )


def test_unregistered_strategy_cannot_enter_active_research() -> None:
    with pytest.raises(ValueError, match="not registered"):
        assert_research_allowed(
            strategy_id="v5.7-another-july-overlay",
            family="new_family",
        )


def test_registered_champion_is_allowed() -> None:
    assert_research_allowed(
        strategy_id="v3.1.2-v3.2-yield-trend",
        family="yielding_cash_trend",
    )
