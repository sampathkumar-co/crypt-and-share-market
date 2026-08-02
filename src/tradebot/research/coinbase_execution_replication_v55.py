from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from tradebot.research import adversarial_alpha_funnel_v52 as v52
from tradebot.research import distributional_utility_v43 as v43
from tradebot.research import forward_tail_integrity_v542 as v542
from tradebot.research import historical_coinbase_replication_v32 as v32
from tradebot.research import untouched_replication_v53 as v53
from tradebot.research import yield_bearing_cash_v44 as v44
from tradebot.research import yield_bearing_cash_v44_reproduce as v44_reproduce
from tradebot.research.regime_ranking_v42 import ASSETS, Dataset
from tradebot.research.regime_ranking_v42_sources import canonical_json, utc_iso

SCHEMA_VERSION = "5.5-coinbase-execution-source-replication"
MANIFEST_SCHEMA_VERSION = "5.5-frozen-binance-decision-manifest"
PROTOCOL_PATH = Path("research/V55_COINBASE_EXECUTION_SOURCE_REPLICATION_PROTOCOL.md")
CONTRACT_PATH = Path("research/V551_COINBASE_EXECUTION_REPLICATION_CONTRACT.md")
DEFAULT_MANIFEST_PATH = Path("research/V55_FROZEN_BINANCE_DECISION_MANIFEST.json")
V542_REPORT_SHA256 = "0219a929a5abf55dbfed719ecad7dbd90bdbda84cab3a2a3d9fb8f72206859d2"
EXPECTED_MANIFEST_SHA256: str | None = None
START = v542.START
END = v542.END
COINBASE_START = v43.day("2026-07-01")
COINBASE_END = v43.day("2026-08-01")
PRODUCTS = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
    "ADA": "ADA-USD",
}


class CoinbaseExecutionReplicationV55Error(RuntimeError):
    pass
def validate_v542_report(report: dict[str, Any]) -> None:
    if report.get("schema_version") != v542.SCHEMA_VERSION:
        raise CoinbaseExecutionReplicationV55Error("unexpected v5.4.2 schema")
    if report.get("report_sha256") != V542_REPORT_SHA256:
        raise CoinbaseExecutionReplicationV55Error("v5.4.2 report hash mismatch")
    if report.get("status") != "FORWARD_TAIL_PASSED":
        raise CoinbaseExecutionReplicationV55Error("v5.4.2 did not pass")
    if len(report.get("decision_dates", [])) != 30:
        raise CoinbaseExecutionReplicationV55Error("v5.4.2 decision count mismatch")
    if not report.get("tail_dataset_integrity", {}).get("exact"):
        raise CoinbaseExecutionReplicationV55Error("v5.4.2 overlap was not exact")
    if report.get("attenuated_rebalance_dates") != ["2026-07-04"]:
        raise CoinbaseExecutionReplicationV55Error("unexpected attenuation date")


def august_opens_from_v542(report: dict[str, Any]) -> dict[str, float]:
    inventory = report["august_exit_open_source"]["inventory"]
    values: dict[str, float] = {}
    for item in inventory:
        parts = item["key"].split(":")
        if len(parts) != 3 or parts[0] != "spot-open":
            raise CoinbaseExecutionReplicationV55Error("invalid August-open key")
        values[parts[1]] = float(item["open"])
    if set(values) != set(ASSETS):
        raise CoinbaseExecutionReplicationV55Error("August-open universe mismatch")
    return values


def build_binance_context(
    baseline_report: dict[str, Any],
    v52_report: dict[str, Any],
    v53_report: dict[str, Any],
    v541_report: dict[str, Any],
    v542_report: dict[str, Any],
    bundle: v43.Bundle,
    *,
    baseline_bundle_sha256: str | None = None,
    base_states: dict[str, dict[datetime, Any]] | None = None,
    base_source_report: dict[str, Any] | None = None,
    cash_history: v44.CashRateHistory | None = None,
    monthly_workers: int = 24,
    metrics_workers: int = 48,
    july_workers: int = 48,
) -> dict[str, Any]:
    v44_reproduce.validate_baseline_report(baseline_report)
    v542.v54.validate_prior_reports(v52_report, v53_report)
    v542.validate_v541_report(v541_report)
    validate_v542_report(v542_report)
    if base_states is None:
        base_states, base_source_report = v542.v54.sources.load_all_sources(
            monthly_workers=monthly_workers,
            metrics_workers=metrics_workers,
        )
    if base_source_report is None:
        raise CoinbaseExecutionReplicationV55Error("base source unavailable")
    if cash_history is None:
        cash_history = v44.load_cash_history()
    reproduction = v44_reproduce.run_reproduction(
        baseline_report,
        bundle,
        states=base_states,
        source_report=base_source_report,
        cash_history=cash_history,
        baseline_bundle_sha256=baseline_bundle_sha256,
    )
    extension, extension_report = v542.v54.load_july_extension(
        max_workers=july_workers,
        downloader=v542.v54.sources.cached_download,
    )
    expected_july = v542_report["july_source"]
    if (
        extension_report["inventory_sha256"]
        != expected_july["inventory_sha256"]
        or extension_report["common_complete_dates"]
        != expected_july["common_complete_dates"]
    ):
        raise CoinbaseExecutionReplicationV55Error("July source mismatch")
    common_dates = [
        datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        for value in extension_report["common_complete_dates"]
    ]
    merged = v542.v54.merge_states(
        base_states,
        v542.v54.common_only(extension, common_dates),
    )
    dataset = v542.build_tail_dataset(
        merged,
        august_opens_from_v542(v542_report),
        evaluation_end=END,
    )
    predictions = v43.predict_components(bundle, dataset.X)
    fold = v542.v54.build_forward_fold(
        dataset,
        bundle,
        predictions,
        cash_history,
        START,
        END,
    )
    raw_active = v52.activity_for_fold(fold, v542.CANDIDATE, {})
    delayed_active = v53.shift_activity(raw_active, 1)
    raw_dates = [
        stamp.date().isoformat()
        for stamp, active in zip(fold.validation_dates, raw_active, strict=True)
        if bool(active)
    ]
    delayed_dates = [
        stamp.date().isoformat()
        for stamp, active in zip(fold.validation_dates, delayed_active, strict=True)
        if bool(active)
    ]
    if raw_dates != v542_report["raw_activity_dates"]:
        raise CoinbaseExecutionReplicationV55Error("raw activity mismatch")
    if delayed_dates != v542_report["delayed_activity_dates"]:
        raise CoinbaseExecutionReplicationV55Error("delayed activity mismatch")
    return {
        "dataset": dataset,
        "predictions": predictions,
        "fold": fold,
        "raw_active": raw_active,
        "delayed_active": delayed_active,
        "cash_history": cash_history,
        "reproduction": reproduction,
        "base_source_report": base_source_report,
        "july_source_report": extension_report,
    }
def build_decision_manifest(
    context: dict[str, Any],
    *,
    v542_report_sha256: str,
    bundle_sha256: str | None,
) -> dict[str, Any]:
    dataset: Dataset = context["dataset"]
    predictions = context["predictions"]
    fold = context["fold"]
    mask = v43.date_mask(dataset, START, END)
    decisions = v43.decisions_by_date(dataset, mask, fold.bundle, predictions)
    raw_by_date = dict(zip(
        fold.validation_dates, context["raw_active"], strict=True
    ))
    delayed_by_date = dict(zip(
        fold.validation_dates, context["delayed_active"], strict=True
    ))
    rows: list[dict[str, Any]] = []
    held: tuple[str, ...] = ()
    age = 3
    for stamp in sorted(decisions):
        decision = decisions[stamp]
        panic = int(decision["regime"]) == 2
        due = age >= 3
        selected = tuple(
            dataset.assets[int(index)] for index in decision["selected"]
        )
        target = held
        if panic:
            target = ()
        elif due:
            target = selected
        changed = target != held
        raw = bool(raw_by_date[stamp])
        delayed = bool(delayed_by_date[stamp])
        selected_rebalance = bool(target) and due
        attenuated = bool(selected_rebalance and delayed and not panic)
        multiplier = 0.0 if panic else (
            float(v542.CANDIDATE.multiplier) if attenuated else 1.0
        )
        rows.append({
            "date": stamp.date().isoformat(),
            "regime": int(decision["regime"]),
            "decision_selected_assets": list(selected),
            "panic": panic,
            "due": due,
            "rebalance": bool(panic or due),
            "selected_rebalance": selected_rebalance,
            "baseline_target_assets": list(target),
            "raw_activity": raw,
            "delayed_activity": delayed,
            "candidate_multiplier": multiplier,
            "attenuated_rebalance": attenuated,
        })
        if panic or due:
            held = target
            if due or (panic and changed):
                age = 0
        age += 1
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "paper_only": True,
        "authorizes_trading": False,
        "decision_period": {
            "start": START.date().isoformat(),
            "end": END.date().isoformat(),
        },
        "v542_report_sha256": v542_report_sha256,
        "bundle_sha256": bundle_sha256,
        "candidate": {
            **v542.asdict(v542.CANDIDATE),
            "activity_delay_days": 1,
        },
        "rows": rows,
    }
    manifest["manifest_sha256"] = hashlib.sha256(
        canonical_json(manifest).encode("utf-8")
    ).hexdigest()
    return manifest


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise CoinbaseExecutionReplicationV55Error("manifest schema mismatch")
    expected = manifest.get("manifest_sha256")
    payload = dict(manifest)
    payload.pop("manifest_sha256", None)
    actual = hashlib.sha256(
        canonical_json(payload).encode("utf-8")
    ).hexdigest()
    if expected != actual:
        raise CoinbaseExecutionReplicationV55Error("manifest self-hash mismatch")
    if EXPECTED_MANIFEST_SHA256 is None:
        raise CoinbaseExecutionReplicationV55Error(
            "manifest hash has not been locked in implementation"
        )
    if actual != EXPECTED_MANIFEST_SHA256:
        raise CoinbaseExecutionReplicationV55Error("manifest lock mismatch")
    rows = manifest.get("rows", [])
    if len(rows) != 30:
        raise CoinbaseExecutionReplicationV55Error("manifest row count mismatch")
    if [row["date"] for row in rows] != [
        (START + timedelta(days=index)).date().isoformat()
        for index in range(30)
    ]:
        raise CoinbaseExecutionReplicationV55Error("manifest dates mismatch")
    attenuated = [row["date"] for row in rows if row["attenuated_rebalance"]]
    if attenuated != ["2026-07-04"]:
        raise CoinbaseExecutionReplicationV55Error("manifest attenuation mismatch")
def required_execution_open_dates() -> list[datetime]:
    return [
        v43.day("2026-07-02") + timedelta(days=index)
        for index in range(31)
    ]


def download_coinbase_opens(
    *,
    downloader: Callable[..., tuple[bytes, str]] = v32._download_json,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, dict[datetime, float]], dict[str, Any]]:
    opens: dict[str, dict[datetime, float]] = {asset: {} for asset in ASSETS}
    inventory: list[dict[str, Any]] = []
    required = required_execution_open_dates()
    for asset in ASSETS:
        product = PRODUCTS[asset]
        url = v32._candle_url(product, COINBASE_START, COINBASE_END)
        content, digest = downloader(url)
        parsed = v32._parse_coinbase_candles(
            content,
            asset=asset,
            requested_start=COINBASE_START,
            requested_end=COINBASE_END,
        )
        missing = [stamp for stamp in required if stamp not in parsed]
        if missing:
            raise CoinbaseExecutionReplicationV55Error(
                f"Coinbase {asset} missing execution open {missing[0].date()}"
            )
        normalized = []
        for stamp in required:
            value = float(parsed[stamp].open)
            if not math.isfinite(value) or value <= 0.0:
                raise CoinbaseExecutionReplicationV55Error(
                    f"Coinbase {asset} invalid open {stamp.date()}"
                )
            opens[asset][stamp] = value
            normalized.append({
                "date": stamp.date().isoformat(),
                "open": value,
            })
        inventory.append({
            "key": f"coinbase:{asset}:2026-07-01-to-2026-08-01",
            "provider": "coinbase-exchange-public-rest",
            "product": product,
            "url": url,
            "response_sha256": digest,
            "parsed_row_count": len(parsed),
            "required_open_count": len(normalized),
            "normalized_open_sha256": hashlib.sha256(
                canonical_json(normalized).encode("utf-8")
            ).hexdigest(),
        })
        sleeper(0.15)
    inventory.sort(key=lambda value: value["key"])
    report = {
        "schema_version": "5.5-coinbase-execution-opens",
        "products": dict(PRODUCTS),
        "required_start": required[0].date().isoformat(),
        "required_end": required[-1].date().isoformat(),
        "required_open_count_per_asset": len(required),
        "successful_asset_count": len(opens),
        "inventory": inventory,
    }
    report["inventory_sha256"] = hashlib.sha256(
        canonical_json(inventory).encode("utf-8")
    ).hexdigest()
    return opens, report


def replace_execution_returns(
    dataset: Dataset,
    opens: dict[str, dict[datetime, float]],
) -> tuple[Dataset, dict[str, Any]]:
    if set(opens) != set(ASSETS):
        raise CoinbaseExecutionReplicationV55Error("Coinbase universe mismatch")
    replaced = dataset.return1.copy()
    positions: list[int] = []
    original_values: list[float] = []
    coinbase_values: list[float] = []
    for index, (stamp, asset) in enumerate(
        zip(dataset.dates, dataset.assets, strict=True)
    ):
        if not START <= stamp <= END:
            continue
        entry = stamp + timedelta(days=1)
        exit_stamp = stamp + timedelta(days=2)
        try:
            value = opens[asset][exit_stamp] / opens[asset][entry] - 1.0
        except KeyError as exc:
            raise CoinbaseExecutionReplicationV55Error(
                f"missing Coinbase execution open for {asset} {stamp.date()}"
            ) from exc
        positions.append(index)
        original_values.append(float(dataset.return1[index]))
        coinbase_values.append(float(value))
        replaced[index] = float(value)
    if len(positions) != 30 * len(ASSETS):
        raise CoinbaseExecutionReplicationV55Error("replaced row count mismatch")
    replica = Dataset(
        X=dataset.X.copy(),
        return1=replaced,
        return3=dataset.return3.copy(),
        return7=dataset.return7.copy(),
        rank3=dataset.rank3.copy(),
        meta=dataset.meta.copy(),
        downside3=dataset.downside3.copy(),
        regimes=dataset.regimes.copy(),
        dates=list(dataset.dates),
        assets=list(dataset.assets),
        feature_names=list(dataset.feature_names),
    )
    correlation = float(np.corrcoef(original_values, coinbase_values)[0, 1])
    if not math.isfinite(correlation):
        correlation = 0.0
    unchanged = np.ones(len(dataset.return1), dtype=bool)
    unchanged[np.asarray(positions, dtype=int)] = False
    report = {
        "replaced_row_count": len(positions),
        "features_exact": bool(np.array_equal(dataset.X, replica.X)),
        "nonreturn_arrays_exact": bool(
            np.array_equal(dataset.return3, replica.return3)
            and np.array_equal(dataset.return7, replica.return7)
            and np.array_equal(dataset.rank3, replica.rank3)
            and np.array_equal(dataset.meta, replica.meta)
            and np.array_equal(dataset.downside3, replica.downside3)
            and np.array_equal(dataset.regimes, replica.regimes)
        ),
        "outside_period_return1_exact": bool(
            np.array_equal(dataset.return1[unchanged], replica.return1[unchanged])
        ),
        "dates_exact": dataset.dates == replica.dates,
        "assets_exact": dataset.assets == replica.assets,
        "feature_names_exact": dataset.feature_names == replica.feature_names,
        "binance_coinbase_return_correlation": correlation,
    }
    report["exact_except_july_return1"] = all(
        value for key, value in report.items()
        if key != "binance_coinbase_return_correlation"
        and key != "replaced_row_count"
    )
    return replica, report
def replication_gates(
    simulation: dict[str, Any],
    replacement: dict[str, Any],
    *,
    manifest_exact: bool,
    source_complete: bool,
) -> dict[str, bool]:
    gates = v542.v54.smoke_gates(simulation, 30)
    gates.pop("minimum_common_dates", None)
    gates.update({
        "coinbase_source_complete": source_complete,
        "decision_manifest_exact": manifest_exact,
        "only_july_return1_replaced": bool(
            replacement["exact_except_july_return1"]
            and replacement["replaced_row_count"] == 30 * len(ASSETS)
        ),
    })
    return gates


def replication_status(
    *,
    source_complete: bool,
    attenuated_decisions: int,
    gates: dict[str, bool] | None,
) -> str:
    if not source_complete:
        return "COINBASE_EXECUTION_DATA_INCONCLUSIVE"
    if attenuated_decisions == 0:
        return "COINBASE_EXECUTION_NO_SIGNAL"
    if gates is not None and all(gates.values()):
        return "COINBASE_EXECUTION_REPLICATION_PASSED"
    return "COINBASE_EXECUTION_REPLICATION_FAILED"


def run_replay(
    baseline_report: dict[str, Any],
    v52_report: dict[str, Any],
    v53_report: dict[str, Any],
    v541_report: dict[str, Any],
    v542_report: dict[str, Any],
    manifest: dict[str, Any],
    bundle: v43.Bundle,
    *,
    baseline_bundle_sha256: str | None = None,
    coinbase_downloader: Callable[..., tuple[bytes, str]] = v32._download_json,
    sleeper: Callable[[float], None] = time.sleep,
    monthly_workers: int = 24,
    metrics_workers: int = 48,
    july_workers: int = 48,
) -> dict[str, Any]:
    validate_manifest(manifest)
    context = build_binance_context(
        baseline_report,
        v52_report,
        v53_report,
        v541_report,
        v542_report,
        bundle,
        baseline_bundle_sha256=baseline_bundle_sha256,
        monthly_workers=monthly_workers,
        metrics_workers=metrics_workers,
        july_workers=july_workers,
    )
    recomputed_manifest = build_decision_manifest(
        context,
        v542_report_sha256=v542_report["report_sha256"],
        bundle_sha256=baseline_bundle_sha256,
    )
    manifest_exact = canonical_json(recomputed_manifest) == canonical_json(manifest)
    if not manifest_exact:
        raise CoinbaseExecutionReplicationV55Error(
            "committed decision manifest did not reproduce"
        )
    opens, source_report = download_coinbase_opens(
        downloader=coinbase_downloader,
        sleeper=sleeper,
    )
    source_complete = bool(
        source_report["successful_asset_count"] == len(ASSETS)
        and all(len(opens[asset]) == 31 for asset in ASSETS)
    )
    execution_dataset, replacement = replace_execution_returns(
        context["dataset"], opens
    )
    probabilities = v53.probabilities_from_activity(
        context["fold"], context["delayed_active"]
    )
    simulation = v53.simulate_window(
        execution_dataset,
        bundle,
        context["predictions"],
        context["cash_history"],
        probabilities,
        v542.CANDIDATE,
        "july-2026-coinbase-execution-replication",
        START,
        END,
    )
    attenuated = int(
        simulation["standard"]["candidate"]["attenuated_decision_count"]
    )
    gates = replication_gates(
        simulation,
        replacement,
        manifest_exact=manifest_exact,
        source_complete=source_complete,
    )
    status = replication_status(
        source_complete=source_complete,
        attenuated_decisions=attenuated,
        gates=gates,
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_iso(datetime.now(timezone.utc)),
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "independent_execution_source": True,
        "signal_source": "frozen-binance-v5.4.2",
        "execution_source": "coinbase-exchange-public-rest",
        "candidate_selection_performed_in_v55": False,
        "candidate_parameters_changed_after_evaluation": False,
        "protocol_sha256": v43.file_sha256(PROTOCOL_PATH),
        "implementation_contract_sha256": v43.file_sha256(CONTRACT_PATH),
        "implementation_sha256": v43.file_sha256(Path(__file__).resolve()),
        "v542_report_sha256": v542_report["report_sha256"],
        "decision_manifest_sha256": manifest["manifest_sha256"],
        "bundle_sha256": baseline_bundle_sha256,
        "source": source_report,
        "decision_manifest_reproduced_exactly": manifest_exact,
        "return_replacement": replacement,
        "simulation": simulation,
        "replication_gates": gates,
        "independent_source_replication_passed": (
            status == "COINBASE_EXECUTION_REPLICATION_PASSED"
        ),
        "accepted_strategy_remains": "v4.4-yield-bearing-cash",
        "status": status,
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run v5.5 Coinbase execution-source replication"
    )
    parser.add_argument("--baseline-json", type=Path, required=True)
    parser.add_argument("--v52-json", type=Path, required=True)
    parser.add_argument("--v53-json", type=Path, required=True)
    parser.add_argument("--v541-json", type=Path, required=True)
    parser.add_argument("--v542-json", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest-only", action="store_true")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("evidence/v55/coinbase-execution.json"),
    )
    parser.add_argument("--monthly-workers", type=int, default=24)
    parser.add_argument("--metrics-workers", type=int, default=48)
    parser.add_argument("--july-workers", type=int, default=48)
    return parser.parse_args(argv)


def read_inputs(args: argparse.Namespace):
    baseline_report = json.loads(
        args.baseline_json.read_text(encoding="utf-8")
    )
    v52_report = json.loads(args.v52_json.read_text(encoding="utf-8"))
    v53_report = json.loads(args.v53_json.read_text(encoding="utf-8"))
    v541_report = json.loads(args.v541_json.read_text(encoding="utf-8"))
    v542_report = json.loads(args.v542_json.read_text(encoding="utf-8"))
    bundle = v44_reproduce.load_bundle(args.bundle)
    bundle_sha256 = v43.file_sha256(args.bundle)
    return (
        baseline_report,
        v52_report,
        v53_report,
        v541_report,
        v542_report,
        bundle,
        bundle_sha256,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    (
        baseline_report,
        v52_report,
        v53_report,
        v541_report,
        v542_report,
        bundle,
        bundle_sha256,
    ) = read_inputs(args)
    if args.manifest_only:
        context = build_binance_context(
            baseline_report,
            v52_report,
            v53_report,
            v541_report,
            v542_report,
            bundle,
            baseline_bundle_sha256=bundle_sha256,
            monthly_workers=max(1, args.monthly_workers),
            metrics_workers=max(1, args.metrics_workers),
            july_workers=max(1, args.july_workers),
        )
        manifest = build_decision_manifest(
            context,
            v542_report_sha256=v542_report["report_sha256"],
            bundle_sha256=bundle_sha256,
        )
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({
            "manifest_sha256": manifest["manifest_sha256"],
            "row_count": len(manifest["rows"]),
            "attenuated_dates": [
                row["date"] for row in manifest["rows"]
                if row["attenuated_rebalance"]
            ],
            "coinbase_download_performed": False,
        }, indent=2, sort_keys=True))
        return 0
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = run_replay(
        baseline_report,
        v52_report,
        v53_report,
        v541_report,
        v542_report,
        manifest,
        bundle,
        baseline_bundle_sha256=bundle_sha256,
        monthly_workers=max(1, args.monthly_workers),
        metrics_workers=max(1, args.metrics_workers),
        july_workers=max(1, args.july_workers),
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    simulation = report["simulation"]
    print(json.dumps({
        "status": report["status"],
        "manifest_exact": report[
            "decision_manifest_reproduced_exactly"
        ],
        "standard_excess": simulation["standard"]["excess_return"],
        "stress_excess": simulation["stress"]["excess_return"],
        "attenuated_decision_count": simulation["standard"][
            "candidate"
        ]["attenuated_decision_count"],
        "return_correlation": report["return_replacement"][
            "binance_coinbase_return_correlation"
        ],
        "report_sha256": report["report_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
