from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from tradebot.research import adversarial_alpha_funnel_v52 as v52
from tradebot.research import distributional_utility_v43 as v43
from tradebot.research import dollar_rates_attenuation_v48 as v48
from tradebot.research import july_forward_smoke_v54 as v54
from tradebot.research import untouched_replication_v53 as v53
from tradebot.research import yield_bearing_cash_v44 as v44
from tradebot.research import yield_bearing_cash_v44_reproduce as v44_reproduce
from tradebot.research.regime_ranking_v42 import (
    ASSETS, FEATURE_NAMES, Dataset, DailyAssetState,
    efficiency, percentile_ranks, rolling_corr, safe_std, state_arrays,
)
from tradebot.research.regime_ranking_v42_sources import canonical_json, utc_iso

SCHEMA_VERSION = "5.4.2-forward-tail-integrity-correction"
PROTOCOL_PATH = Path("research/V542_FORWARD_TAIL_INTEGRITY_CORRECTION_PROTOCOL.md")
CONTRACT_PATH = Path("research/V5421_FORWARD_TAIL_INTEGRITY_IMPLEMENTATION_CONTRACT.md")
START = v43.day("2026-07-01")
END = v43.day("2026-07-30")
AUGUST_OPEN_DAY = v43.day("2026-08-01")
EXPECTED_DECISION_DATES = 30
V541_REPORT_SHA256 = "18a67cd667f5a68a2eb97b74c610c668e211bb99fd2683d0778d4700caf26ea3"
CANDIDATE = v53.PRIMARY
STANDARD_ONE_WAY_COST = v48.STANDARD_ONE_WAY_COST
STRESS_ONE_WAY_COST = v48.STRESS_ONE_WAY_COST


class ForwardTailIntegrityV542Error(RuntimeError):
    pass

def build_tail_dataset(
    states: dict[str, dict[datetime, DailyAssetState]],
    next_open_by_asset: dict[str, float],
    *,
    evaluation_end: datetime,
) -> Dataset:
    dates, arrays = state_arrays(states)
    if len(dates) < 202:
        raise ForwardTailIntegrityV542Error("insufficient complete common dates")
    if evaluation_end not in dates:
        raise ForwardTailIntegrityV542Error("evaluation end missing from common dates")
    end_index = dates.index(evaluation_end)
    if end_index + 1 >= len(dates):
        raise ForwardTailIntegrityV542Error("entry open missing after evaluation end")
    if set(next_open_by_asset) != set(ASSETS):
        raise ForwardTailIntegrityV542Error("next-open universe mismatch")
    spot_closes = np.vstack([
        arrays[asset]["spot_close"] for asset in ASSETS
    ])
    perp_closes = np.vstack([
        arrays[asset]["perp_close"] for asset in ASSETS
    ])
    spot_opens = np.vstack([
        arrays[asset]["spot_open"] for asset in ASSETS
    ])
    spot_lows = np.vstack([
        arrays[asset]["spot_low"] for asset in ASSETS
    ])
    spot_log_returns = np.diff(
        np.log(spot_closes), axis=1, prepend=np.log(spot_closes[:, :1])
    )
    rows: list[list[float]] = []
    labels1: list[float] = []
    labels3: list[float] = []
    labels7: list[float] = []
    ranks3: list[float] = []
    metas: list[int] = []
    downsides: list[int] = []
    regimes: list[int] = []
    row_dates: list[datetime] = []
    row_assets: list[str] = []

    for index in range(199, end_index + 1):
        if dates[index] - dates[index - 199] != timedelta(days=199):
            continue
        if dates[index + 1] - dates[index] != timedelta(days=1):
            continue
        if index + 2 < len(dates):
            if dates[index + 2] - dates[index] != timedelta(days=2):
                continue
            exit_open = spot_opens[:, index + 2]
        else:
            if dates[index] != evaluation_end:
                raise ForwardTailIntegrityV542Error("unexpected missing exit open")
            exit_open = np.asarray([next_open_by_asset[a] for a in ASSETS], dtype=float)
        market_close = np.mean(spot_closes, axis=0)
        market_return7 = float(
            market_close[index] / market_close[index - 7] - 1.0
        )
        market_return30 = float(
            market_close[index] / market_close[index - 30] - 1.0
        )
        breadth20 = float(np.mean([
            spot_closes[pos, index]
            > np.mean(spot_closes[pos, index - 19:index + 1])
            for pos in range(len(ASSETS))
        ]))
        breadth100 = float(np.mean([
            spot_closes[pos, index]
            > np.mean(spot_closes[pos, index - 99:index + 1])
            for pos in range(len(ASSETS))
        ]))
        momentum30 = (
            spot_closes[:, index] / spot_closes[:, index - 30] - 1.0
        )
        dispersion30 = float(np.std(momentum30))
        entry_open = spot_opens[:, index + 1]
        future1 = exit_open / entry_open - 1.0
        future3 = np.zeros(len(ASSETS), dtype=float)
        future7 = np.zeros(len(ASSETS), dtype=float)
        regime = 0
        realized_rank3 = np.zeros(len(ASSETS), dtype=float)
        top_two: set[int] = set()
        basis_change7 = np.asarray([
            arrays[asset]["basis"][index]
            - arrays[asset]["basis"][index - 7]
            for asset in ASSETS
        ])
        funding_now = np.asarray([
            arrays[asset]["funding"][index] for asset in ASSETS
        ])
        oi_change7 = np.asarray([
            arrays[asset]["open_interest"][index]
            / arrays[asset]["open_interest"][index - 7]
            - 1.0
            for asset in ASSETS
        ])
        volatility30 = np.asarray([
            safe_std(spot_log_returns[pos, index - 29:index + 1])
            for pos in range(len(ASSETS))
        ])
        flow_now = np.asarray([
            arrays[asset]["spot_flow"][index] for asset in ASSETS
        ])
        rank_features = {
            "momentum": percentile_ranks(momentum30),
            "basis": percentile_ranks(basis_change7),
            "funding": percentile_ranks(funding_now),
            "oi": percentile_ranks(oi_change7),
            "volatility": percentile_ranks(volatility30),
            "flow": percentile_ranks(flow_now),
        }
        pairwise: list[float] = []
        for left in range(len(ASSETS)):
            for right in range(left + 1, len(ASSETS)):
                pairwise.append(rolling_corr(
                    spot_log_returns[left, index - 29:index + 1],
                    spot_log_returns[right, index - 29:index + 1],
                ))
        average_correlation30 = float(np.mean(pairwise))
        long_build = (momentum30 > 0.0) & (oi_change7 > 0.0)
        short_build = (momentum30 < 0.0) & (oi_change7 > 0.0)
        long_liquidation = (momentum30 < 0.0) & (oi_change7 < 0.0)
        short_covering = (momentum30 > 0.0) & (oi_change7 < 0.0)
        recovery_state = np.asarray([
            spot_closes[pos, index] / spot_closes[pos, index - 30] - 1.0
            < -0.08
            and spot_closes[pos, index] / spot_closes[pos, index - 7] - 1.0
            > 0.0
            for pos in range(len(ASSETS))
        ])
        market_features = [
            float(spot_closes[0, index] / spot_closes[0, index - 30] - 1.0),
            safe_std(spot_log_returns[0, index - 29:index + 1]),
            float(spot_closes[0, index] > np.mean(spot_closes[0, index - 99:index + 1])),
            market_return7,
            market_return30,
            breadth20,
            breadth100,
            dispersion30,
        ]
        market_features.extend([
            float(np.median(funding_now)),
            float(np.median(oi_change7)),
            average_correlation30,
            float(np.mean(long_build)),
            float(np.mean(long_liquidation)),
            float(np.mean(recovery_state)),
        ])

        for pos, asset in enumerate(ASSETS):
            values = arrays[asset]
            spot = values["spot_close"]
            perp = values["perp_close"]
            funding = values["funding"]
            open_interest = values["open_interest"]
            spot_volume = values["spot_volume"]
            perp_volume = values["perp_volume"]
            spot_returns = {
                days: float(spot[index] / spot[index - days] - 1.0)
                for days in (1, 3, 7, 14, 30, 60, 120)
            }
            perp_returns = {
                days: float(perp[index] / perp[index - days] - 1.0)
                for days in (1, 3, 7, 14, 30, 60, 120)
            }
            oi_changes = {
                days: float(
                    open_interest[index] / open_interest[index - days] - 1.0
                )
                for days in (1, 3, 7, 30)
            }
            funding_window30 = funding[index - 29:index + 1]
            funding_z30 = float(
                (funding_window30[-1] - np.mean(funding_window30))
                / safe_std(funding_window30)
            )
            sign_persistence = float(
                abs(np.mean(np.sign(funding_window30)))
            )
            beta_corr: list[float] = []
            for window in (30, 90):
                left = spot_log_returns[pos, index - window + 1:index + 1]
                right = spot_log_returns[0, index - window + 1:index + 1]
                covariance = float(np.cov(left, right, ddof=0)[0, 1])
                beta_corr.extend([
                    covariance / max(float(np.var(right)), 1e-12),
                    rolling_corr(left, right),
                ])
            state_values = [
                float(long_build[pos]),
                float(short_build[pos]),
                float(long_liquidation[pos]),
                float(short_covering[pos]),
            ]
            volume_features = [
                float(spot_volume[index] / spot_volume[index - days] - 1.0)
                for days in (1, 7, 30)
            ]
            volume_features.extend([
                float(perp_volume[index] / perp_volume[index - days] - 1.0)
                for days in (1, 7, 30)
            ])
            row = [
                *(spot_returns[days] for days in (1, 3, 7, 14, 30, 60, 120)),
                *(perp_returns[days] for days in (1, 3, 7, 14, 30, 60, 120)),
                *(spot_returns[days] - perp_returns[days]
                  for days in (1, 3, 7, 14, 30, 60, 120)),
                float(365.0 * values["basis"][index]),
                *(float(values["basis"][index] - values["basis"][index - days])
                  for days in (1, 3, 7, 30)),
                float(funding[index]),
                float(np.mean(funding[index - 2:index + 1])),
                float(np.mean(funding[index - 6:index + 1])),
                funding_z30,
                sign_persistence,
                *(oi_changes[days] for days in (1, 3, 7, 30)),
                *state_values,
                *volume_features,
                float(values["spot_flow"][index]),
                float(np.mean(values["spot_flow"][index - 2:index + 1])),
                float(np.mean(values["spot_flow"][index - 6:index + 1])),
                float(values["perp_flow"][index]),
                float(np.mean(values["perp_flow"][index - 2:index + 1])),
                float(np.mean(values["perp_flow"][index - 6:index + 1])),
                float(values["spot_flow"][index] - values["perp_flow"][index]),
            ]
            row.extend([
                safe_std(spot_log_returns[pos, index - 6:index + 1]),
                safe_std(spot_log_returns[pos, index - 29:index + 1]),
                safe_std(spot_log_returns[pos, index - 89:index + 1]),
                *(float(spot[index] / np.mean(spot[index - days + 1:index + 1]) - 1.0)
                  for days in (20, 50, 100, 200)),
                efficiency(spot[index - 13:index + 1]),
                efficiency(spot[index - 59:index + 1]),
                beta_corr[0],
                beta_corr[1],
                beta_corr[2],
                beta_corr[3],
                float(rank_features["momentum"][pos]),
                float(rank_features["basis"][pos]),
                float(rank_features["funding"][pos]),
                float(rank_features["oi"][pos]),
                float(rank_features["volatility"][pos]),
                float(rank_features["flow"][pos]),
                *market_features,
            ])
            row.extend(1.0 if asset == candidate else 0.0 for candidate in ASSETS)
            if len(row) != len(FEATURE_NAMES):
                raise ForwardTailIntegrityV542Error(
                    f"feature width mismatch: {len(row)} != {len(FEATURE_NAMES)}"
                )
            rows.append(row)
            labels1.append(float(future1[pos]))
            labels3.append(float(future3[pos]))
            labels7.append(float(future7[pos]))
            ranks3.append(float(realized_rank3[pos]))
            metas.append(0)
            downsides.append(0)
            regimes.append(0)
            row_dates.append(dates[index])
            row_assets.append(asset)

    X = np.asarray(rows, dtype=float)
    if not len(X):
        raise ForwardTailIntegrityV542Error("no complete feature rows")
    if not np.all(np.isfinite(X)):
        raise ForwardTailIntegrityV542Error("nonfinite feature matrix")
    return Dataset(
        X=X,
        return1=np.asarray(labels1, dtype=float),
        return3=np.asarray(labels3, dtype=float),
        return7=np.asarray(labels7, dtype=float),
        rank3=np.asarray(ranks3, dtype=float),
        meta=np.asarray(metas, dtype=int),
        downside3=np.asarray(downsides, dtype=int),
        regimes=np.asarray(regimes, dtype=int),
        dates=row_dates,
        assets=row_assets,
        feature_names=FEATURE_NAMES,
    )


def validate_v541_report(report: dict[str, Any]) -> None:
    if report.get("schema_version") != v54.SCHEMA_VERSION:
        raise ForwardTailIntegrityV542Error("unexpected v5.4.1 schema")
    if report.get("report_sha256") != V541_REPORT_SHA256:
        raise ForwardTailIntegrityV542Error("v5.4.1 report hash mismatch")
    if report.get("status") != "FORWARD_SMOKE_PASSED":
        raise ForwardTailIntegrityV542Error("v5.4.1 was not the recorded partial pass")
    simulation = report.get("simulation") or {}
    days = simulation.get("standard", {}).get("candidate", {}).get(
        "decision_count"
    )
    if days != 23:
        raise ForwardTailIntegrityV542Error("unexpected v5.4.1 partial decision count")


def august_spot_url(asset: str) -> str:
    symbol = v54.sources.SYMBOLS[asset]
    day = AUGUST_OPEN_DAY.date().isoformat()
    return (
        f"{v54.sources.BASE_URL}/spot/daily/klines/{symbol}/1d/"
        f"{symbol}-1d-{day}.zip"
    )


def load_august_exit_opens(
    *,
    downloader: Callable[..., v54.sources.DownloadedArchive | None] = (
        v54.sources.cached_download
    ),
) -> tuple[dict[str, float], dict[str, Any]]:
    opens: dict[str, float] = {}
    inventory: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for asset in ASSETS:
        url = august_spot_url(asset)
        archive = downloader(url, optional_404=True)
        if archive is None:
            missing.append({"asset": asset, "url": url, "reason": "404"})
            continue
        try:
            bars = v54.sources.parse_daily_klines(archive)
            bar = bars[AUGUST_OPEN_DAY]
            value = float(bar.open)
            if not np.isfinite(value) or value <= 0.0:
                raise ForwardTailIntegrityV542Error("invalid August open")
        except Exception as exc:
            missing.append({
                "asset": asset,
                "url": url,
                "reason": f"parse:{type(exc).__name__}:{exc}",
            })
            continue
        opens[asset] = value
        inventory.append({
            "key": f"spot-open:{asset}:2026-08-01",
            "url": url,
            "sha256": archive.sha256,
            "open": value,
        })
    inventory.sort(key=lambda value: value["key"])
    missing.sort(key=lambda value: value["asset"])
    report = {
        "schema_version": "5.4.2-binance-august-1-exit-opens",
        "requested_archive_count": len(ASSETS),
        "successful_archive_count": len(inventory),
        "missing_count": len(missing),
        "inventory": inventory,
        "missing": missing,
    }
    report["inventory_sha256"] = hashlib.sha256(
        canonical_json(inventory).encode("utf-8")
    ).hexdigest()
    return opens, report
def overlap_integrity(
    generic: Dataset,
    tail: Dataset,
) -> dict[str, Any]:
    generic_keys = list(zip(generic.dates, generic.assets, strict=True))
    tail_keys = list(zip(tail.dates, tail.assets, strict=True))
    generic_index = {key: index for index, key in enumerate(generic_keys)}
    tail_index = {key: index for index, key in enumerate(tail_keys)}
    shared = [key for key in generic_keys if key in tail_index]
    missing = [key for key in generic_keys if key not in tail_index]
    generic_positions = np.asarray(
        [generic_index[key] for key in shared], dtype=int
    )
    tail_positions = np.asarray([tail_index[key] for key in shared], dtype=int)
    features_exact = bool(
        np.array_equal(generic.X[generic_positions], tail.X[tail_positions])
    )
    return1_exact = bool(
        np.array_equal(
            generic.return1[generic_positions],
            tail.return1[tail_positions],
        )
    )
    shared_order_exact = shared == [
        key for key in tail_keys if key in generic_index
    ]
    return {
        "generic_row_count": len(generic_keys),
        "tail_row_count": len(tail_keys),
        "shared_row_count": len(shared),
        "generic_rows_missing_from_tail": len(missing),
        "feature_names_exact": generic.feature_names == tail.feature_names,
        "shared_order_exact": shared_order_exact,
        "features_exact": features_exact,
        "return1_exact": return1_exact,
        "exact": bool(
            not missing
            and generic.feature_names == tail.feature_names
            and shared_order_exact
            and features_exact
            and return1_exact
        ),
    }


def decision_dates(dataset: Dataset) -> list[datetime]:
    return sorted({
        stamp for stamp in dataset.dates if START <= stamp <= END
    })


def correction_gates(
    simulation: dict[str, Any],
    overlap: dict[str, Any],
    dates: list[datetime],
    august_report: dict[str, Any],
) -> dict[str, bool]:
    gates = v54.smoke_gates(simulation, len(dates))
    gates.update({
        "exact_generic_overlap": bool(overlap["exact"]),
        "exactly_thirty_decision_dates": (
            len(dates) == EXPECTED_DECISION_DATES
            and dates[0] == START
            and dates[-1] == END
        ),
        "five_complete_august_exit_opens": (
            august_report["successful_archive_count"] == len(ASSETS)
            and august_report["missing_count"] == 0
        ),
    })
    return gates


def correction_status(
    dates: list[datetime],
    august_report: dict[str, Any],
    attenuated_decisions: int,
    gates: dict[str, bool] | None,
) -> str:
    if (
        len(dates) != EXPECTED_DECISION_DATES
        or august_report["successful_archive_count"] != len(ASSETS)
        or august_report["missing_count"] != 0
    ):
        return "FORWARD_TAIL_DATA_INCONCLUSIVE"
    if attenuated_decisions == 0:
        return "FORWARD_TAIL_NO_SIGNAL"
    if gates is not None and all(gates.values()):
        return "FORWARD_TAIL_PASSED"
    return "FORWARD_TAIL_FAILED"
def run_campaign(
    baseline_report: dict[str, Any],
    v52_report: dict[str, Any],
    v53_report: dict[str, Any],
    v541_report: dict[str, Any],
    bundle: v43.Bundle,
    *,
    baseline_bundle_sha256: str | None = None,
    base_states: dict[str, dict[datetime, v54.sources.DailyAssetState]] | None = None,
    base_source_report: dict[str, Any] | None = None,
    cash_history: v44.CashRateHistory | None = None,
    july_downloader: Callable[..., v54.sources.DownloadedArchive | None] = (
        v54.sources.cached_download
    ),
    august_downloader: Callable[..., v54.sources.DownloadedArchive | None] = (
        v54.sources.cached_download
    ),
    monthly_workers: int = 24,
    metrics_workers: int = 48,
    july_workers: int = 48,
) -> dict[str, Any]:
    v44_reproduce.validate_baseline_report(baseline_report)
    v54.validate_prior_reports(v52_report, v53_report)
    validate_v541_report(v541_report)
    if base_states is None:
        base_states, base_source_report = v54.sources.load_all_sources(
            monthly_workers=monthly_workers,
            metrics_workers=metrics_workers,
        )
    if base_source_report is None:
        raise ForwardTailIntegrityV542Error("base source report unavailable")
    if base_source_report.get("source_end") != "2026-06-30":
        raise ForwardTailIntegrityV542Error("unexpected frozen base source end")
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
    extension, extension_report = v54.load_july_extension(
        max_workers=july_workers,
        downloader=july_downloader,
    )
    expected_july = v541_report["july_source"]
    july_source_exact = bool(
        extension_report["inventory_sha256"]
        == expected_july["inventory_sha256"]
        and extension_report["common_complete_dates"]
        == expected_july["common_complete_dates"]
        and extension_report["missing"] == expected_july["missing"]
    )
    august_opens, august_report = load_august_exit_opens(
        downloader=august_downloader
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_iso(datetime.now(timezone.utc)),
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "forward_only": True,
        "candidate_selection_performed_in_v542": False,
        "candidate_parameters_changed_after_evaluation": False,
        "candidate": {
            **asdict(CANDIDATE),
            "activity_delay_days": 1,
        },
        "requested_decision_period": {
            "start": utc_iso(START),
            "end": utc_iso(END),
        },
        "protocol_sha256": v43.file_sha256(PROTOCOL_PATH),
        "implementation_contract_sha256": v43.file_sha256(CONTRACT_PATH),
        "implementation_sha256": v43.file_sha256(Path(__file__).resolve()),
        "v52_report_sha256": v52_report["report_sha256"],
        "v53_report_sha256": v53_report["report_sha256"],
        "v541_partial_report_sha256": v541_report["report_sha256"],
        "baseline_report_sha256": baseline_report["report_sha256"],
        "baseline_bundle_sha256": baseline_bundle_sha256,
        "v44_reproduction_report_sha256": reproduction["report_sha256"],
        "base_source": base_source_report,
        "july_source": extension_report,
        "july_source_exactly_reproduced": july_source_exact,
        "august_exit_open_source": august_report,
        "cash_source": cash_history.source,
        "runtime": v48.runtime_versions(),
        "reproduction": {
            **reproduction["reproduction"],
            "final_bundle_training_end": utc_iso(v43.TRAIN_END),
            "candidate_retrained": False,
            "candidate_recalibrated": False,
            "synthetic_future_labels_used": False,
        },
        "v541_partial_smoke": {
            "preserved": True,
            "decision_count": 23,
            "superseded_only_for_date_coverage": True,
        },
        "accepted_strategy_remains": "v4.4-yield-bearing-cash",
    }
    common_dates = [
        datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        for value in extension_report["common_complete_dates"]
    ]
    source_ready = bool(
        july_source_exact
        and len(common_dates) == 31
        and common_dates[0] == v54.START
        and common_dates[-1] == v54.END
        and august_report["successful_archive_count"] == len(ASSETS)
        and august_report["missing_count"] == 0
    )
    if not source_ready:
        report.update({
            "tail_dataset_integrity": None,
            "decision_dates": [],
            "raw_activity_dates": [],
            "delayed_activity_dates": [],
            "attenuated_rebalance_dates": [],
            "simulation": None,
            "correction_gates": None,
            "current_market_smoke_passed": False,
            "status": "FORWARD_TAIL_DATA_INCONCLUSIVE",
        })
        report["report_sha256"] = hashlib.sha256(
            canonical_json(report).encode("utf-8")
        ).hexdigest()
        return report
    filtered_extension = v54.common_only(extension, common_dates)
    merged_states = v54.merge_states(base_states, filtered_extension)
    generic_dataset = v54.build_dataset(merged_states)
    tail_dataset = build_tail_dataset(
        merged_states,
        august_opens,
        evaluation_end=END,
    )
    overlap = overlap_integrity(generic_dataset, tail_dataset)
    dates = decision_dates(tail_dataset)
    if not overlap["exact"]:
        raise ForwardTailIntegrityV542Error(
            "tail inference rows differ from generic historical rows"
        )
    predictions = v43.predict_components(bundle, tail_dataset.X)
    fold = v54.build_forward_fold(
        tail_dataset,
        bundle,
        predictions,
        cash_history,
        START,
        END,
    )
    raw_active = v52.activity_for_fold(fold, CANDIDATE, {})
    delayed_active = v53.shift_activity(raw_active, 1)
    probabilities = v53.probabilities_from_activity(fold, delayed_active)
    simulation = v53.simulate_window(
        tail_dataset,
        bundle,
        predictions,
        cash_history,
        probabilities,
        CANDIDATE,
        "july-2026-forward-tail-correction",
        START,
        END,
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
    gates = correction_gates(simulation, overlap, dates, august_report)
    status = correction_status(
        dates,
        august_report,
        attenuated_decisions,
        gates,
    )
    smoke_passed = status == "FORWARD_TAIL_PASSED"
    report.update({
        "tail_dataset_integrity": overlap,
        "decision_dates": [stamp.date().isoformat() for stamp in dates],
        "raw_activity_dates": raw_activity_dates,
        "delayed_activity_dates": delayed_activity_dates,
        "attenuated_rebalance_dates": attenuated_rebalance_dates,
        "simulation": simulation,
        "correction_gates": gates,
        "current_market_smoke_passed": smoke_passed,
        "status": status,
    })
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run v5.4.2 forward-tail integrity correction"
    )
    parser.add_argument("--baseline-json", type=Path, required=True)
    parser.add_argument("--v52-json", type=Path, required=True)
    parser.add_argument("--v53-json", type=Path, required=True)
    parser.add_argument("--v541-json", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("evidence/v542/forward-tail.json"),
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
    v541_report = json.loads(args.v541_json.read_text(encoding="utf-8"))
    bundle = v44_reproduce.load_bundle(args.bundle)
    report = run_campaign(
        baseline_report,
        v52_report,
        v53_report,
        v541_report,
        bundle,
        baseline_bundle_sha256=v43.file_sha256(args.bundle),
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
        "decision_date_count": len(report["decision_dates"]),
        "overlap_exact": (
            None if report["tail_dataset_integrity"] is None
            else report["tail_dataset_integrity"]["exact"]
        ),
        "attenuated_decision_count": (
            0 if simulation is None
            else simulation["standard"]["candidate"][
                "attenuated_decision_count"
            ]
        ),
        "standard_excess": (
            None if simulation is None
            else simulation["standard"]["excess_return"]
        ),
        "stress_excess": (
            None if simulation is None
            else simulation["stress"]["excess_return"]
        ),
        "report_sha256": report["report_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
