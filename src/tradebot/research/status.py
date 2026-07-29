from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

LEDGER_SCHEMA_VERSION = "1.0"
DEFAULT_LEDGER_PATH = Path("research/experiment_ledger.json")


def _safe_status(path: Path, reason: str) -> dict[str, Any]:
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "source_valid": False,
        "source_path": str(path),
        "source_fingerprint": None,
        "deployment_mode": "research_only",
        "paper_only": True,
        "approved_strategies": [],
        "approved_strategy_count": 0,
        "continuous_paper_authorized": False,
        "live_trading_authorized": False,
        "experiments": [],
        "decision": "NO_STRATEGY_APPROVED",
        "reason": reason,
    }


def configured_ledger_path(path: str | Path | None = None) -> Path:
    raw = path if path is not None else os.getenv("TRADEBOT_RESEARCH_LEDGER", str(DEFAULT_LEDGER_PATH))
    return Path(raw).expanduser().resolve()


def load_research_status(path: str | Path | None = None) -> dict[str, Any]:
    ledger_path = configured_ledger_path(path)
    if not ledger_path.exists() or not ledger_path.is_file():
        return _safe_status(ledger_path, "Research ledger is missing; authorization fails closed.")
    try:
        raw = ledger_path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _safe_status(ledger_path, f"Research ledger is unreadable or malformed: {exc}")
    if not isinstance(payload, dict):
        return _safe_status(ledger_path, "Research ledger root must be a JSON object.")
    approved = payload.get("approved_strategies")
    experiments = payload.get("experiments")
    invalid_reasons: list[str] = []
    if payload.get("schema_version") != LEDGER_SCHEMA_VERSION:
        invalid_reasons.append("unsupported schema version")
    if payload.get("paper_only") is not True:
        invalid_reasons.append("paper_only must be true")
    if payload.get("live_trading_authorized") is not False:
        invalid_reasons.append("live trading authorization is forbidden")
    if not isinstance(approved, list) or not all(isinstance(item, str) and item.strip() for item in approved):
        invalid_reasons.append("approved_strategies must be a list of non-empty strings")
        approved = []
    if not isinstance(experiments, list):
        invalid_reasons.append("experiments must be a list")
        experiments = []
    continuous = payload.get("continuous_paper_authorized") is True
    if continuous and not approved:
        invalid_reasons.append("continuous paper authorization requires an approved strategy")
    if invalid_reasons:
        return _safe_status(ledger_path, "Invalid research ledger: " + "; ".join(invalid_reasons))
    normalized = dict(payload)
    normalized.update(
        {
            "source_valid": True,
            "source_path": str(ledger_path),
            "source_fingerprint": hashlib.sha256(raw).hexdigest(),
            "approved_strategies": sorted(set(item.strip() for item in approved)),
            "approved_strategy_count": len(set(item.strip() for item in approved)),
            "continuous_paper_authorized": continuous,
            "live_trading_authorized": False,
            "experiments": experiments,
        }
    )
    return normalized


def require_continuous_paper_authorization(
    strategy_name: str,
    path: str | Path | None = None,
) -> dict[str, Any]:
    status = load_research_status(path)
    if not status.get("source_valid"):
        raise ValueError(str(status.get("reason", "Research ledger validation failed")))
    if status.get("live_trading_authorized") is not False:
        raise ValueError("Research ledger attempted to authorize live trading")
    if status.get("continuous_paper_authorized") is not True:
        raise ValueError("Continuous paper trading is not authorized by the research ledger")
    if strategy_name not in status.get("approved_strategies", []):
        raise ValueError(f"Strategy {strategy_name} is not approved by the research ledger")
    return status
