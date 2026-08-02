from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from tradebot.research import adversarial_alpha_funnel_v52 as v52
from tradebot.research import distributional_utility_v43 as v43
from tradebot.research import dollar_rates_attenuation_v48 as v48
from tradebot.research import untouched_replication_v53 as v53
from tradebot.research import yield_bearing_cash_v44 as v44
from tradebot.research import yield_bearing_cash_v44_reproduce as v44_reproduce
from tradebot.research import regime_ranking_v42_sources as sources
from tradebot.research.regime_ranking_v42 import (
    ASSETS,
    STANDARD_ONE_WAY_COST,
    STRESS_ONE_WAY_COST,
    Dataset,
    build_dataset,
    file_sha256,
)
from tradebot.research.regime_ranking_v42_sources import canonical_json, utc_iso

SCHEMA_VERSION = "5.4-july-forward-paper-smoke"
PROTOCOL_PATH = Path("research/V54_JULY_FORWARD_SMOKE_PROTOCOL.md")
CONTRACT_PATH = Path("research/V541_JULY_FORWARD_SMOKE_CONTRACT.md")
V52_REPORT_SHA256 = v53.V52_REPORT_SHA256
V53_REPORT_SHA256 = (
    "133917e3b52367d34b51ca5f7958d3c"
    "be1f982903669570140937b55be7197ea"
)
START = v43.day("2026-07-01")
END = v43.day("2026-07-31")
MINIMUM_COMMON_DATES = 29
CANDIDATE = v53.PRIMARY


class JulyForwardSmokeV54Error(RuntimeError):
    pass


def july_dates() -> list[datetime]:
    result: list[datetime] = []
    current = START
    while current <= END:
        result.append(current)
        current += timedelta(days=1)
    return result


def daily_urls(asset: str, stamp: datetime) -> dict[str, str]:
    symbol = sources.SYMBOLS[asset]
    day = stamp.date().isoformat()
    base = sources.BASE_URL
    return {
        "spot": (
            f"{base}/spot/daily/klines/{symbol}/1d/"
            f"{symbol}-1d-{day}.zip"
        ),
        "perp": (
            f"{base}/futures/um/daily/klines/{symbol}/1d/"
            f"{symbol}-1d-{day}.zip"
        ),
        "funding": (
            f"{base}/futures/um/daily/fundingRate/{symbol}/"
            f"{symbol}-fundingRate-{day}.zip"
        ),
        "metrics": (
            f"{base}/futures/um/daily/metrics/{symbol}/"
            f"{symbol}-metrics-{day}.zip"
        ),
    }


def validate_prior_reports(
    v52_report: dict[str, Any],
    v53_report: dict[str, Any],
) -> None:
    v53.validate_v52_report(v52_report)
    if v53_report.get("schema_version") != v53.SCHEMA_VERSION:
        raise JulyForwardSmokeV54Error("unexpected v5.3 schema")
    if v53_report.get("report_sha256") != V53_REPORT_SHA256:
        raise JulyForwardSmokeV54Error("v5.3 report hash mismatch")
    if v53_report.get("primary", {}).get("hypothesis") != asdict(CANDIDATE):
        raise JulyForwardSmokeV54Error("v5.3 primary mismatch")
    if v53_report.get("status") != "UNTOUCHED_MECHANISM_REPLICATION_FAILED":
        raise JulyForwardSmokeV54Error("unexpected v5.3 decision")


def load_july_extension(
    *,
    max_workers: int = 48,
    downloader: Callable[..., sources.DownloadedArchive | None] = (
        sources.cached_download
    ),
) -> tuple[
    dict[str, dict[datetime, sources.DailyAssetState]],
    dict[str, Any],
]:
    requests: list[tuple[str, str, datetime, str]] = []
    for asset in ASSETS:
        for stamp in july_dates():
            for kind, url in daily_urls(asset, stamp).items():
                requests.append((kind, asset, stamp, url))
    downloaded: dict[
        tuple[str, str, datetime], sources.DownloadedArchive
    ] = {}
    inventory: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                downloader, url, optional_404=True
            ): (kind, asset, stamp, url)
            for kind, asset, stamp, url in requests
        }
        for future in as_completed(futures):
            kind, asset, stamp, url = futures[future]
            archive = future.result()
            key = f"{kind}:{asset}:{stamp.date().isoformat()}"
            if archive is None:
                missing.append({"key": key, "url": url, "reason": "404"})
                continue
            downloaded[(kind, asset, stamp)] = archive
            inventory.append({
                "key": key,
                "url": url,
                "sha256": archive.sha256,
            })
    extension = {asset: {} for asset in ASSETS}
    for asset in ASSETS:
        for stamp in july_dates():
            required = {
                kind: downloaded.get((kind, asset, stamp))
                for kind in ("spot", "perp", "funding", "metrics")
            }
            absent = [kind for kind, archive in required.items() if archive is None]
            if absent:
                continue
            try:
                spot = sources.parse_daily_klines(required["spot"])[stamp]
                perp = sources.parse_daily_klines(required["perp"])[stamp]
                funding = sources.aggregate_daily_funding(
                    required["funding"]
                )[stamp]
                open_interest = sources.parse_daily_open_interest(
                    required["metrics"]
                )
                if open_interest is None:
                    raise JulyForwardSmokeV54Error(
                        "open interest has no positive observation"
                    )
            except Exception as exc:
                missing.append({
                    "key": f"state:{asset}:{stamp.date().isoformat()}",
                    "url": "",
                    "reason": f"parse:{type(exc).__name__}:{exc}",
                })
                continue
            extension[asset][stamp] = sources.DailyAssetState(
                day=stamp,
                spot=spot,
                perp=perp,
                funding=float(funding),
                open_interest=float(open_interest),
                basis=perp.close / spot.close - 1.0,
                spot_flow=sources.flow_imbalance(spot),
                perp_flow=sources.flow_imbalance(perp),
            )
    common_dates = sorted(set.intersection(*[
        set(extension[asset]) for asset in ASSETS
    ]))
    inventory = sorted(inventory, key=lambda value: value["key"])
    missing = sorted(missing, key=lambda value: value["key"])
    report = {
        "schema_version": "5.4-binance-july-daily-extension",
        "requested_start": START.date().isoformat(),
        "requested_end": END.date().isoformat(),
        "requested_archive_count": len(requests),
        "successful_archive_count": len(inventory),
        "missing_component_count": len(missing),
        "common_complete_date_count": len(common_dates),
        "common_complete_dates": [
            stamp.date().isoformat() for stamp in common_dates
        ],
        "complete_dates_by_asset": {
            asset: len(extension[asset]) for asset in ASSETS
        },
        "inventory": inventory,
        "missing": missing,
    }
    report["inventory_sha256"] = hashlib.sha256(
        canonical_json(inventory).encode("utf-8")
    ).hexdigest()
    return extension, report


def merge_states(
    base: dict[str, dict[datetime, sources.DailyAssetState]],
    extension: dict[str, dict[datetime, sources.DailyAssetState]],
) -> dict[str, dict[datetime, sources.DailyAssetState]]:
    merged = {asset: dict(base[asset]) for asset in ASSETS}
    for asset in ASSETS:
        overlap = set(merged[asset]) & set(extension[asset])
        if overlap:
            raise JulyForwardSmokeV54Error(
                f"July extension overlaps frozen history for {asset}"
            )
        merged[asset].update(extension[asset])
    return merged


def common_only(
    extension: dict[str, dict[datetime, sources.DailyAssetState]],
    dates: list[datetime],
) -> dict[str, dict[datetime, sources.DailyAssetState]]:
    allowed = set(dates)
    return {
        asset: {
            stamp: value
            for stamp, value in extension[asset].items()
            if stamp in allowed
        }
        for asset in ASSETS
    }


def build_forward_fold(
    dataset: Dataset,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    cash_history: v44.CashRateHistory,
    start: datetime,
    end: datetime,
) -> v52.FoldData:
    panel_start = start - timedelta(days=230)
    panel_dates = sorted({
        stamp for stamp in dataset.dates
        if panel_start <= stamp <= end
    })
    panel = v52.market_panel(dataset, panel_dates)
    validation_mask = v43.date_mask(dataset, start, end)
    decisions = v43.decisions_by_date(
        dataset, validation_mask, bundle, predictions
    )
    validation_dates, rebalance, selected_rebalance = (
        v52.baseline_schedule(decisions)
    )
    positions = {stamp: index for index, stamp in enumerate(panel_dates)}
    validation_positions = np.asarray(
        [positions[stamp] for stamp in validation_dates], dtype=int
    )
    baseline = v44.simulate(
        dataset,
        validation_mask,
        bundle,
        predictions,
        cash_history,
        one_way_cost=STANDARD_ONE_WAY_COST,
    )
    baseline_daily = np.asarray(baseline["daily_returns"], dtype=float)
    cash_daily = np.asarray([
        v44.annual_to_daily_rate(
            v44.prior_known_annual_rate(cash_history, stamp)[1]
        )
        for stamp in validation_dates
    ], dtype=float)
    if len(baseline_daily) != len(validation_dates):
        raise JulyForwardSmokeV54Error("baseline daily-return mismatch")
    return v52.FoldData(
        name="july-2026-forward-smoke",
        panel_dates=panel_dates,
        validation_dates=validation_dates,
        validation_positions=validation_positions,
        panel=panel,
        baseline=baseline,
        baseline_daily_returns=baseline_daily,
        risky_daily_returns=baseline_daily - cash_daily,
        cash_daily_returns=cash_daily,
        rebalance_mask=rebalance,
        selected_rebalance_mask=selected_rebalance,
        bundle=bundle,
        predictions=predictions,
        validation_mask=validation_mask,
    )


def smoke_gates(result: dict[str, Any], common_date_count: int) -> dict[str, bool]:
    standard = result["standard"]
    stress = result["stress"]
    standard_candidate = standard["candidate"]
    stress_candidate = stress["candidate"]
    standard_baseline = standard["baseline"]
    stress_baseline = stress["baseline"]
    return {
        "minimum_common_dates": common_date_count >= MINIMUM_COMMON_DATES,
        "one_attenuated_decision": (
            int(standard_candidate["attenuated_decision_count"]) >= 1
        ),
        "standard_excess_positive": float(standard["excess_return"]) > 0.0,
        "stress_excess_positive": float(stress["excess_return"]) > 0.0,
        "standard_drawdown_not_worse_by_25bp": (
            float(standard_candidate["maximum_drawdown"])
            <= float(standard_baseline["maximum_drawdown"]) + 0.0025
        ),
        "stress_drawdown_not_worse_by_25bp": (
            float(stress_candidate["maximum_drawdown"])
            <= float(stress_baseline["maximum_drawdown"]) + 0.0025
        ),
        "standard_actions_not_increased": (
            int(standard_candidate["target_changing_actions"])
            <= int(standard_baseline["target_changing_actions"])
        ),
        "stress_actions_not_increased": (
            int(stress_candidate["target_changing_actions"])
            <= int(stress_baseline["target_changing_actions"])
        ),
        "never_added_asset": bool(
            standard_candidate["never_added_asset"]
            and stress_candidate["never_added_asset"]
        ),
        "never_increased_target": bool(
            standard_candidate["never_increased_target"]
            and stress_candidate["never_increased_target"]
        ),
    }


def result_status(
    common_date_count: int,
    attenuated_decisions: int,
    gates: dict[str, bool] | None,
) -> str:
    if common_date_count < MINIMUM_COMMON_DATES:
        return "FORWARD_SMOKE_DATA_INCONCLUSIVE"
    if attenuated_decisions == 0:
        return "FORWARD_SMOKE_NO_SIGNAL"
    if gates is not None and all(gates.values()):
        return "FORWARD_SMOKE_PASSED"
    return "FORWARD_SMOKE_FAILED"


def run_campaign(
    baseline_report: dict[str, Any],
    v52_report: dict[str, Any],
    v53_report: dict[str, Any],
    bundle: v43.Bundle,
    *,
    baseline_bundle_sha256: str | None = None,
    base_states: dict[str, dict[datetime, sources.DailyAssetState]] | None = None,
    base_source_report: dict[str, Any] | None = None,
    cash_history: v44.CashRateHistory | None = None,
    july_downloader: Callable[..., sources.DownloadedArchive | None] = (
        sources.cached_download
    ),
    monthly_workers: int = 24,
    metrics_workers: int = 48,
    july_workers: int = 48,
) -> dict[str, Any]:
    v44_reproduce.validate_baseline_report(baseline_report)
    validate_prior_reports(v52_report, v53_report)
    if base_states is None:
        base_states, base_source_report = sources.load_all_sources(
            monthly_workers=monthly_workers,
            metrics_workers=metrics_workers,
        )
    if base_source_report is None:
        raise JulyForwardSmokeV54Error("base source report unavailable")
    if base_source_report.get("source_end") != "2026-06-30":
        raise JulyForwardSmokeV54Error("unexpected frozen base source end")
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
    extension, extension_report = load_july_extension(
        max_workers=july_workers,
        downloader=july_downloader,
    )
    common_dates = [
        datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        for value in extension_report["common_complete_dates"]
    ]
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_iso(datetime.now(timezone.utc)),
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "forward_only": True,
        "july_data_downloaded_after_protocol_freeze": True,
        "candidate_selection_performed_in_v54": False,
        "candidate_parameters_changed_after_evaluation": False,
        "candidate": {
            **asdict(CANDIDATE),
            "activity_delay_days": 1,
        },
        "requested_period": {
            "start": utc_iso(START),
            "end": utc_iso(END),
        },
        "protocol_sha256": file_sha256(PROTOCOL_PATH),
        "implementation_contract_sha256": file_sha256(CONTRACT_PATH),
        "implementation_sha256": file_sha256(Path(__file__).resolve()),
        "v52_report_sha256": v52_report["report_sha256"],
        "v53_report_sha256": v53_report["report_sha256"],
        "baseline_report_sha256": baseline_report["report_sha256"],
        "baseline_bundle_sha256": baseline_bundle_sha256,
        "v44_reproduction_report_sha256": reproduction["report_sha256"],
        "base_source": base_source_report,
        "july_source": extension_report,
        "cash_source": cash_history.source,
        "runtime": v48.runtime_versions(),
        "reproduction": {
            **reproduction["reproduction"],
            "final_bundle_training_end": utc_iso(v43.TRAIN_END),
            "cash_rate_carried_from_prior_known_history": True,
            "candidate_retrained": False,
            "candidate_recalibrated": False,
        },
        "accepted_strategy_remains": "v4.4-yield-bearing-cash",
    }
    common_count = len(common_dates)
    if common_count < MINIMUM_COMMON_DATES:
        report.update({
            "evaluated_period": None,
            "raw_activity_dates": [],
            "delayed_activity_dates": [],
            "attenuated_rebalance_dates": [],
            "simulation": None,
            "smoke_gates": None,
            "current_market_smoke_passed": False,
            "status": "FORWARD_SMOKE_DATA_INCONCLUSIVE",
        })
        report["report_sha256"] = hashlib.sha256(
            canonical_json(report).encode("utf-8")
        ).hexdigest()
        return report
    filtered_extension = common_only(extension, common_dates)
    merged_states = merge_states(base_states, filtered_extension)
    dataset = build_dataset(merged_states)
    predictions = v43.predict_components(bundle, dataset.X)
    evaluation_start = common_dates[0]
    evaluation_end = common_dates[-1]
    fold = build_forward_fold(
        dataset,
        bundle,
        predictions,
        cash_history,
        evaluation_start,
        evaluation_end,
    )
    raw_active = v52.activity_for_fold(fold, CANDIDATE, {})
    delayed_active = v53.shift_activity(raw_active, 1)
    probabilities = v53.probabilities_from_activity(
        fold, delayed_active
    )
    simulation = v53.simulate_window(
        dataset,
        bundle,
        predictions,
        cash_history,
        probabilities,
        CANDIDATE,
        "july-2026-forward-smoke",
        evaluation_start,
        evaluation_end,
    )
    raw_activity_dates = [
        stamp.date().isoformat()
        for stamp, active in zip(
            fold.validation_dates, raw_active, strict=True
        )
        if bool(active)
    ]
    delayed_activity_dates = [
        stamp.date().isoformat()
        for stamp, active in zip(
            fold.validation_dates, delayed_active, strict=True
        )
        if bool(active)
    ]
    attenuated_rebalance_dates = [
        stamp.date().isoformat()
        for stamp, active, selected in zip(
            fold.validation_dates,
            delayed_active,
            fold.selected_rebalance_mask,
            strict=True,
        )
        if bool(active and selected)
    ]
    attenuated_decisions = int(
        simulation["standard"]["candidate"]["attenuated_decision_count"]
    )
    gates = smoke_gates(simulation, common_count)
    status = result_status(common_count, attenuated_decisions, gates)
    smoke_passed = status == "FORWARD_SMOKE_PASSED"
    report.update({
        "authorizes_shadow_paper": smoke_passed,
        "evaluated_period": {
            "start": utc_iso(evaluation_start),
            "end": utc_iso(evaluation_end),
            "common_complete_date_count": common_count,
        },
        "raw_activity_dates": raw_activity_dates,
        "delayed_activity_dates": delayed_activity_dates,
        "attenuated_rebalance_dates": attenuated_rebalance_dates,
        "simulation": simulation,
        "smoke_gates": gates,
        "current_market_smoke_passed": smoke_passed,
        "status": status,
    })
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run frozen v5.4 July forward paper smoke"
    )
    parser.add_argument("--baseline-json", type=Path, required=True)
    parser.add_argument("--v52-json", type=Path, required=True)
    parser.add_argument("--v53-json", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("evidence/v54/july-forward.json"),
    )
    parser.add_argument("--monthly-workers", type=int, default=24)
    parser.add_argument("--metrics-workers", type=int, default=48)
    parser.add_argument("--july-workers", type=int, default=48)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    baseline_report = json.loads(
        args.baseline_json.read_text(encoding="utf-8")
    )
    v52_report = json.loads(args.v52_json.read_text(encoding="utf-8"))
    v53_report = json.loads(args.v53_json.read_text(encoding="utf-8"))
    bundle = v44_reproduce.load_bundle(args.bundle)
    report = run_campaign(
        baseline_report,
        v52_report,
        v53_report,
        bundle,
        baseline_bundle_sha256=file_sha256(args.bundle),
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
    standard_excess = (
        None if simulation is None
        else simulation["standard"]["excess_return"]
    )
    stress_excess = (
        None if simulation is None
        else simulation["stress"]["excess_return"]
    )
    attenuated = (
        0 if simulation is None
        else simulation["standard"]["candidate"][
            "attenuated_decision_count"
        ]
    )
    print(json.dumps({
        "status": report["status"],
        "common_complete_date_count": report["july_source"][
            "common_complete_date_count"
        ],
        "evaluated_period": report["evaluated_period"],
        "attenuated_decision_count": attenuated,
        "standard_excess": standard_excess,
        "stress_excess": stress_excess,
        "current_market_smoke_passed": report[
            "current_market_smoke_passed"
        ],
        "report_sha256": report["report_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
