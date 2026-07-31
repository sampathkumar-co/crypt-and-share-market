from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import historical_yield_trend_v31 as v31
from tradebot.research import historical_yield_trend_v311_transport as cash_transport
from tradebot.research import historical_yield_trend_scheduled_execution_v312 as execution

MODE = "HISTORICAL_YIELD_TREND_SCHEDULED_EXECUTION_AUDIT_ONLY"
SCHEMA_VERSION = "3.1.2-scheduled-rebalance-integrity"
ADDENDUM_PATH = Path("research/V312_SCHEDULED_REBALANCE_INTEGRITY_ADDENDUM.md")
RESULT_STATUS_PASS = "VERIFIED_EXECUTION_CORRECTED_BINANCE_CANDIDATE"
RESULT_STATUS_FAIL = "NOT_VERIFIED_EXECUTION_CORRECTED_BINANCE_CANDIDATE"
SOURCE_V31_REPORT_SHA256 = (
    "8015b7a37597800e595ac7ed2ae1145af05c35baccbd63d17a87928f6b8eaf61"
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


class YieldTrendIntegrityV312Error(RuntimeError):
    """Raised when the v3.1.2 integrity audit cannot run safely."""


def run_integrity_audit(max_workers: int = 16) -> dict[str, Any]:
    if not ADDENDUM_PATH.is_file():
        raise YieldTrendIntegrityV312Error(f"missing addendum: {ADDENDUM_PATH}")
    if FROZEN_MODEL.model_id != EXPECTED_MODEL_ID:
        raise YieldTrendIntegrityV312Error("frozen selected model changed")

    original_downloader = v31._download_fred
    v31._download_fred = cash_transport.download_cash_series_with_resilience
    try:
        downloaded, normalized_cash, inventory = v31.download_inputs(
            max_workers=max_workers
        )
    finally:
        v31._download_fred = original_downloader

    bars, dates = v31.assemble_bars(downloaded)
    features = v31.build_features(bars, dates)
    rates = cash_transport.parse_fred_rates(normalized_cash)
    cash_returns = v31.build_daily_cash_returns(rates, dates)

    standard_results: dict[str, v31.SimulationResult] = {}
    stress_results: dict[str, v31.SimulationResult] = {}
    for period in v31.VERIFICATION_PERIODS:
        standard_results[period.name] = execution.simulate_scheduled(
            FROZEN_MODEL,
            bars,
            features,
            cash_returns,
            period.start,
            period.end,
            v31.STANDARD_COST,
        )
        stress_results[period.name] = execution.simulate_scheduled(
            FROZEN_MODEL,
            bars,
            features,
            cash_returns,
            period.start,
            period.end,
            v31.STRESS_COST,
        )

    standard = execution.summarize_years(standard_results)
    stress = execution.summarize_years(stress_results)
    gates = execution.evaluate_integrity_gates(standard, stress)
    accepted = all(gates.values())
    inventory = list(inventory)
    inventory.sort(key=lambda item: item["key"])
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "changes_track_a": False,
        "cannot_replace_forward_evidence": True,
        "price_provider": "binance-public-vision-spot-archives",
        "cash_series": "DGS3MO",
        "cash_source_policy": cash_transport.CASH_SOURCE_POLICY,
        "cash_transport_policy": cash_transport.CASH_TRANSPORT_POLICY,
        "cash_transport_audit": dict(cash_transport.TRANSPORT_AUDIT),
        "source_v31_report_sha256": SOURCE_V31_REPORT_SHA256,
        "execution_policy": (
            "daily_risk_exit_entry_or_due_rebalance_only_natural_drift"
        ),
        "assets": list(v31.ASSETS),
        "frozen_model": asdict(FROZEN_MODEL) | {
            "model_id": FROZEN_MODEL.model_id
        },
        "verification_periods": [
            {
                "name": period.name,
                "start": v31._utc(period.start),
                "end": v31._utc(period.end),
            }
            for period in v31.VERIFICATION_PERIODS
        ],
        "source_inventory": inventory,
        "source_inventory_sha256": hashlib.sha256(
            canonical_json(inventory).encode("utf-8")
        ).hexdigest(),
        "standard": standard,
        "stress": stress,
        "gates": gates,
        "screening_status": (
            RESULT_STATUS_PASS if accepted else RESULT_STATUS_FAIL
        ),
        "fingerprints": {
            "integrity_addendum_sha256": hashlib.sha256(
                ADDENDUM_PATH.read_bytes()
            ).hexdigest(),
            "implementation_sha256": hashlib.sha256(
                Path(__file__).resolve().read_bytes()
            ).hexdigest(),
            "scheduled_execution_sha256": hashlib.sha256(
                Path(execution.__file__).resolve().read_bytes()
            ).hexdigest(),
            "v31_engine_sha256": hashlib.sha256(
                Path(v31.__file__).resolve().read_bytes()
            ).hexdigest(),
            "cash_transport_sha256": hashlib.sha256(
                Path(cash_transport.__file__).resolve().read_bytes()
            ).hexdigest(),
            "frozen_model_sha256": hashlib.sha256(
                canonical_json(asdict(FROZEN_MODEL)).encode("utf-8")
            ).hexdigest(),
        },
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run v3.1.2 scheduled-rebalance integrity audit."
    )
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--max-workers", type=int, default=16)
    args = parser.parse_args(argv)
    report = run_integrity_audit(max_workers=args.max_workers)
    _atomic_json(Path(args.json_out), report)
    print(
        json.dumps(
            {
                "status": report["screening_status"],
                "standard_return": report["standard"][
                    "net_compounded_return"
                ],
                "stress_return": report["stress"]["net_compounded_return"],
                "cash_return": report["standard"][
                    "cash_benchmark_compounded_return"
                ],
                "standard_years": report["standard"]["window_returns"],
                "stress_years": report["stress"]["window_returns"],
                "standard_excess_years": report["standard"][
                    "excess_window_returns"
                ],
                "stress_excess_years": report["stress"][
                    "excess_window_returns"
                ],
                "action_days": report["standard"]["window_action_days"],
                "gates": report["gates"],
                "report_sha256": report["report_sha256"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
