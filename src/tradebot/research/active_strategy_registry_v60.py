from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any


REGISTRY_PATH = Path(__file__).resolve().parents[3] / "research" / "ACTIVE_STRATEGY_REGISTRY_V60.json"


@dataclass(frozen=True)
class StrategyBoundary:
    active_ids: frozenset[str]
    retired_families: frozenset[str]
    paper_only: bool
    authorizes_trading: bool
    authorizes_continuous_paper: bool


def load_strategy_boundary(path: Path = REGISTRY_PATH) -> StrategyBoundary:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    active_ids = {
        payload["historical_champion"]["strategy_id"],
        payload["active_forward_program"]["strategy_id"],
        *(item["strategy_id"] for item in payload["controls"]),
        *(item["strategy_id"] for item in payload["challengers"]),
        *(item["component_id"] for item in payload["libraries"]),
    }
    boundary = StrategyBoundary(
        active_ids=frozenset(active_ids),
        retired_families=frozenset(payload["retired_families"]),
        paper_only=bool(payload["paper_only"]),
        authorizes_trading=bool(payload["authorizes_trading"]),
        authorizes_continuous_paper=bool(payload["authorizes_continuous_paper"]),
    )
    validate_strategy_boundary(boundary)
    return boundary


def validate_strategy_boundary(boundary: StrategyBoundary) -> None:
    if not boundary.paper_only:
        raise ValueError("v6 registry must remain paper-only")
    if boundary.authorizes_trading:
        raise ValueError("v6 registry cannot authorize trading")
    if boundary.authorizes_continuous_paper:
        raise ValueError("continuous paper remains locked")
    if not boundary.active_ids:
        raise ValueError("at least one active research role is required")
    if not boundary.retired_families:
        raise ValueError("retired families must be explicit")


def assert_research_allowed(*, strategy_id: str, family: str) -> None:
    boundary = load_strategy_boundary()
    if family in boundary.retired_families:
        raise ValueError(f"retired strategy family cannot be reopened: {family}")
    if strategy_id not in boundary.active_ids:
        raise ValueError(f"strategy is not registered for active v6 research: {strategy_id}")
