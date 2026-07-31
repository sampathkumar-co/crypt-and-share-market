from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import forward_yield_trend_v33_sources as sources
from tradebot.research import historical_coinbase_replication_v32 as v32
from tradebot.research import historical_yield_trend_v31 as v31
from tradebot.research import historical_yield_trend_scheduled_execution_v312 as execution

MODE = "DAILY_FORWARD_YIELD_TREND_OBSERVATION_ONLY"
SCHEMA_VERSION = "3.3-daily-forward-observation"
MANIFEST_SCHEMA_VERSION = "3.3-daily-forward-source-manifest"
PROTOCOL_PATH = Path("research/V33_DAILY_FORWARD_OBSERVATION_PROTOCOL.md")
BINANCE_REPORT_SHA256 = (
    "90dea7bcc12274146f730ba5a5cd9f93179ff944211ff07de849aca68e468c22"
)
COINBASE_REPORT_SHA256 = (
    "c8a2bf7204681cdd5ce642886a42ea361f016008d908cfa16d299798cb9fefc4"
)
SCHEDULED_EXECUTION_SHA256 = (
    "f19eb507942e022bae0c42a271b8ba709cf808677b48358fbf1429692e56a71a"
)
FROZEN_MODEL = v31.ModelSpec(
    sma_length=100,
    rebalance_days=10,
    top_n=1,
    maximum_exposure=0.10,
    volatility_target=0.02,
    drawdown_brake=0.20,
)
EXPECTED_MODEL_ID = "sma100-rebalance10-top1-exposure10-vol2-brake20"
TARGET_CHANGING_ACTIONS = {"ENTER", "REBALANCE", "EXIT"}
VALID_ACTIONS = TARGET_CHANGING_ACTIONS | {
    "HOLD_NO_TRADE",
    "CASH_NO_TRADE",
    "GAP_RESET_NO_TRADE",
}


class ForwardYieldTrendV33Error(RuntimeError):
    pass


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_previous(
    folder: Path,
    completed_day: datetime,
) -> tuple[dict[str, Any] | None, str | None, bool]:
    if not folder.exists():
        return None, None, True
    candidates: list[tuple[datetime, Path]] = []
    for path in folder.glob("*.json"):
        try:
            day = datetime.fromisoformat(path.stem).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if day < completed_day:
            candidates.append((day, path))
    if not candidates:
        return None, None, True
    prior_day, prior_path = max(candidates, key=lambda item: item[0])
    payload = json.loads(prior_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(prior_path.read_bytes()).hexdigest()
    contiguous = prior_day == completed_day - timedelta(days=1)
    return payload, digest, contiguous


def _feature_dict(feature: v31.Features) -> dict[str, float]:
    return {key: float(value) for key, value in asdict(feature).items()}


def decide(
    features: dict[str, v31.Features],
    previous: dict[str, Any] | None,
    contiguous: bool,
) -> dict[str, Any]:
    if previous is not None and not contiguous:
        return {
            "action": "GAP_RESET_NO_TRADE",
            "trade_required": False,
            "recommended_target_weights": None,
            "minimum_cash_target": None,
            "state_after": {
                "sleeve": "cash",
                "selected_assets": [],
                "age": 0,
            },
            "decision_reason": "prior_calendar_day_observation_missing",
        }

    prior = (
        previous["state_after"]
        if previous is not None
        else {"sleeve": "cash", "selected_assets": [], "age": 0}
    )
    prior_sleeve = str(prior["sleeve"])
    prior_assets = tuple(str(item) for item in prior["selected_assets"])
    prior_age = int(prior["age"])
    proposed, selected, sleeve, age = v31._target(
        FROZEN_MODEL,
        features,
        prior_assets,
        prior_sleeve,
        prior_age,
    )
    due = (
        prior_sleeve != "trend"
        or prior_age >= FROZEN_MODEL.rebalance_days - 1
    )

    if sleeve == "cash":
        if prior_sleeve == "trend":
            action = "EXIT"
            target: dict[str, float] | None = {}
            cash_target: float | None = 1.0
            reason = "daily_risk_off_exit"
            trade_required = True
        else:
            action = "CASH_NO_TRADE"
            target = None
            cash_target = None
            reason = "risk_conditions_not_qualified"
            trade_required = False
        state = {"sleeve": "cash", "selected_assets": [], "age": 0}
    elif due:
        target = {key: float(value) for key, value in sorted(proposed.items())}
        exposure = sum(target.values())
        if len(target) > 1 or exposure > 0.10 + 1e-12:
            raise ForwardYieldTrendV33Error("target exposure contract violated")
        action = "ENTER" if prior_sleeve != "trend" else "REBALANCE"
        cash_target = 1.0 - exposure
        reason = (
            "qualified_entry_from_cash"
            if action == "ENTER"
            else "scheduled_ten_day_rebalance"
        )
        trade_required = True
        state = {
            "sleeve": "trend",
            "selected_assets": list(selected),
            "age": int(age),
        }
    else:
        if tuple(selected) != prior_assets:
            raise ForwardYieldTrendV33Error(
                "selected asset changed before scheduled rebalance"
            )
        action = "HOLD_NO_TRADE"
        target = None
        cash_target = None
        reason = "natural_drift_until_scheduled_rebalance"
        trade_required = False
        state = {
            "sleeve": "trend",
            "selected_assets": list(prior_assets),
            "age": int(age),
        }

    return {
        "action": action,
        "trade_required": trade_required,
        "recommended_target_weights": target,
        "minimum_cash_target": cash_target,
        "state_after": state,
        "decision_reason": reason,
    }


def build_observation(
    *,
    as_of: datetime,
    history_folder: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not PROTOCOL_PATH.is_file():
        raise ForwardYieldTrendV33Error(f"missing protocol: {PROTOCOL_PATH}")
    if FROZEN_MODEL.model_id != EXPECTED_MODEL_ID:
        raise ForwardYieldTrendV33Error("frozen model identity changed")
    actual_execution_sha = hashlib.sha256(
        Path(execution.__file__).resolve().read_bytes()
    ).hexdigest()
    if actual_execution_sha != SCHEDULED_EXECUTION_SHA256:
        raise ForwardYieldTrendV33Error(
            "scheduled execution implementation fingerprint changed"
        )

    completed_day = sources.latest_completed_day(as_of)
    bars, raw_coinbase, normalized = sources.fetch_coinbase_history(
        completed_day
    )
    start = completed_day - timedelta(days=sources.HISTORY_DAYS - 1)
    dates = [start + timedelta(days=index) for index in range(sources.HISTORY_DAYS)]
    feature_history = v31.build_features(bars, dates)
    if completed_day not in feature_history:
        raise ForwardYieldTrendV33Error("completed-day features unavailable")
    features = feature_history[completed_day]
    cash_evidence, raw_cash = sources.fetch_h15_evidence(completed_day)
    previous, previous_sha, contiguous = load_previous(
        history_folder,
        completed_day,
    )
    result = decide(features, previous, contiguous)
    if result["action"] not in VALID_ACTIONS:
        raise ForwardYieldTrendV33Error("unsupported observation action")

    state_before = (
        previous["state_after"]
        if previous is not None and contiguous
        else {"sleeve": "cash", "selected_assets": [], "age": 0}
    )
    effective_open = completed_day + timedelta(days=2)
    observation: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "changes_track_a": False,
        "cannot_replace_forward_evidence": True,
        "completed_candle_date_utc": completed_day.date().isoformat(),
        "data_cutoff_utc": _utc(
            completed_day + timedelta(days=1) - timedelta(microseconds=1)
        ),
        "earliest_eligible_effective_open_utc": _utc(effective_open),
        "operational_latency_days": 1,
        "price_provider": "coinbase-exchange-public-rest",
        "products": v32.PRODUCTS,
        "history_days": sources.HISTORY_DAYS,
        "frozen_model": asdict(FROZEN_MODEL) | {
            "model_id": FROZEN_MODEL.model_id
        },
        "historical_promotion_evidence": {
            "corrected_binance_report_sha256": BINANCE_REPORT_SHA256,
            "coinbase_replication_report_sha256": COINBASE_REPORT_SHA256,
        },
        "source_fingerprints": {
            "coinbase_btc_normalized_sha256": normalized["BTC"],
            "coinbase_eth_normalized_sha256": normalized["ETH"],
            "h15_normalized_sha256": cash_evidence["normalized_sha256"],
            "prior_observation_file_sha256": previous_sha,
        },
        "cash_evidence": cash_evidence,
        "features": {
            asset: _feature_dict(feature)
            for asset, feature in sorted(features.items())
        },
        "state_before": state_before,
        "continuity": {
            "has_prior_observation": previous is not None,
            "prior_calendar_day_present": contiguous,
            "gap_reset": result["action"] == "GAP_RESET_NO_TRADE",
        },
        **result,
        "target_changing_action": result["action"] in TARGET_CHANGING_ACTIONS,
        "fingerprints": {
            "protocol_sha256": hashlib.sha256(
                PROTOCOL_PATH.read_bytes()
            ).hexdigest(),
            "implementation_sha256": hashlib.sha256(
                Path(__file__).resolve().read_bytes()
            ).hexdigest(),
            "source_implementation_sha256": hashlib.sha256(
                Path(sources.__file__).resolve().read_bytes()
            ).hexdigest(),
            "scheduled_execution_sha256": actual_execution_sha,
            "frozen_model_sha256": hashlib.sha256(
                canonical_json(asdict(FROZEN_MODEL)).encode("utf-8")
            ).hexdigest(),
        },
    }
    observation["report_sha256"] = hashlib.sha256(
        canonical_json(observation).encode("utf-8")
    ).hexdigest()

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "completed_candle_date_utc": completed_day.date().isoformat(),
        "observation_report_sha256": observation["report_sha256"],
        "source_inventory": sorted(
            [*raw_coinbase, raw_cash],
            key=lambda item: item["key"],
        ),
    }
    manifest["source_inventory_sha256"] = hashlib.sha256(
        canonical_json(manifest["source_inventory"]).encode("utf-8")
    ).hexdigest()
    return observation, manifest


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a v3.3 observation")
    parser.add_argument("--history-folder", required=True)
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--manifest-out", required=True)
    parser.add_argument("--as-of-utc")
    args = parser.parse_args(argv)
    as_of = (
        datetime.fromisoformat(args.as_of_utc.replace("Z", "+00:00"))
        if args.as_of_utc
        else datetime.now(timezone.utc)
    )
    observation, manifest = build_observation(
        as_of=as_of,
        history_folder=Path(args.history_folder),
    )
    _write(Path(args.json_out), observation)
    _write(Path(args.manifest_out), manifest)
    print(
        json.dumps(
            {
                "completed_candle_date_utc": observation[
                    "completed_candle_date_utc"
                ],
                "action": observation["action"],
                "reason": observation["decision_reason"],
                "trade_required": observation["trade_required"],
                "earliest_eligible_effective_open_utc": observation[
                    "earliest_eligible_effective_open_utc"
                ],
                "state_after": observation["state_after"],
                "report_sha256": observation["report_sha256"],
                "authorizes_trading": False,
                "authorizes_shadow_paper": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
