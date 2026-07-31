from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from tradebot.research import forward_alpha_v25 as v25
from tradebot.research.market_state_router import SnapshotFrame, load_forward_snapshots


SCHEMA_VERSION = "2.5-historical-screening"
PROTOCOL_PATH = Path("research/V25_HISTORICAL_SCREENING_PROTOCOL.md")
INITIAL_EQUITY = 100_000.0
HORIZONS = (2, 4, 8)
STANDARD_ROUND_TRIP_BPS = 20.0
STRESS_ROUND_TRIP_BPS = 40.0
FAMILIES = (
    "residual_momentum_microstructure",
    "funding_basis_state_transition",
    "sweep_replenishment_continuation",
)


class HistoricalScreeningError(RuntimeError):
    """Raised when historical screening evidence is unsafe or inconsistent."""


@dataclass(frozen=True)
class ReplayEvent:
    decision_hour: datetime
    entry_hour: datetime
    asset: str
    family: str
    target_weight: float
    event_key: str


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hour(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _canonical_frames(frames: Iterable[SnapshotFrame]) -> list[SnapshotFrame]:
    ordered = sorted(frames, key=lambda frame: (frame.hour, frame.captured_at, frame.snapshot_id))
    by_hour: dict[datetime, SnapshotFrame] = {}
    for frame in ordered:
        prior = by_hour.get(frame.hour)
        if prior is None or (frame.captured_at, frame.snapshot_id) < (prior.captured_at, prior.snapshot_id):
            by_hour[frame.hour] = frame
    return [by_hour[hour] for hour in sorted(by_hour)]


def _continuous_blocks(frames: list[SnapshotFrame]) -> list[list[SnapshotFrame]]:
    blocks: list[list[SnapshotFrame]] = []
    current: list[SnapshotFrame] = []
    for frame in frames:
        if current and frame.hour - current[-1].hour != timedelta(hours=1):
            blocks.append(current)
            current = []
        current.append(frame)
    if current:
        blocks.append(current)
    return blocks


def _spot_mid(frame: SnapshotFrame, asset: str) -> float:
    values = v25._asset_values(frame, asset)
    if values is None:
        raise HistoricalScreeningError(f"Missing v2.5 spot-mid inputs for {asset} at {_hour(frame.hour)}")
    price = float(values["spot_mid"])
    if price <= 0.0:
        raise HistoricalScreeningError(f"Non-positive spot mid for {asset} at {_hour(frame.hour)}")
    return price


def _max_drawdown(equity_curve: list[float]) -> float:
    peak = 0.0
    worst = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0.0:
            worst = max(worst, 1.0 - equity / peak)
    return worst


def _fingerprints() -> dict[str, str]:
    source = Path(__file__).resolve()
    if not PROTOCOL_PATH.is_file():
        raise HistoricalScreeningError(f"Missing historical protocol: {PROTOCOL_PATH}")
    return {
        "screening_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "screening_protocol_sha256": hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest(),
        "frozen_v25": v25.implementation_fingerprints(),
    }


def _replay_decisions(blocks: list[list[SnapshotFrame]]) -> tuple[list[ReplayEvent], dict[str, int]]:
    events: list[ReplayEvent] = []
    diagnostics = {
        "eligible_decision_hours": 0,
        "cash_decision_hours": 0,
        "selected_candidate_rows": 0,
        "cooldown_suppressed_rows": 0,
    }
    for block in blocks:
        if len(block) < 178:
            continue
        last_event: dict[tuple[str, str], datetime] = {}
        for index in range(168, len(block) - 9):
            window = block[index - 168 : index + 1]
            report = v25.evaluate_forward_alpha_v25(window, as_of=block[index].hour)
            diagnostics["eligible_decision_hours"] += 1
            selected = report["selected_candidates"]
            if not selected:
                diagnostics["cash_decision_hours"] += 1
                continue
            diagnostics["selected_candidate_rows"] += len(selected)
            for candidate in selected:
                asset = str(candidate["asset"])
                family = str(candidate["family"])
                if family not in FAMILIES:
                    raise HistoricalScreeningError(f"Unexpected v2.5 family: {family}")
                key = (asset, family)
                prior = last_event.get(key)
                if prior is not None and block[index].hour - prior < timedelta(hours=4):
                    diagnostics["cooldown_suppressed_rows"] += 1
                    continue
                event = ReplayEvent(
                    decision_hour=block[index].hour,
                    entry_hour=block[index + 1].hour,
                    asset=asset,
                    family=family,
                    target_weight=float(candidate["target_weight"]),
                    event_key=str(candidate["event_key"]),
                )
                if not 0.0 < event.target_weight <= 0.15 + 1e-12:
                    raise HistoricalScreeningError("Historical event violates the frozen 15% cap")
                last_event[key] = event.decision_hour
                events.append(event)
    return events, diagnostics


def _simulate(
    blocks: list[list[SnapshotFrame]],
    events: list[ReplayEvent],
    *,
    horizon: int,
    round_trip_bps: float,
    included_families: set[str] | None = None,
) -> dict[str, Any]:
    if horizon not in HORIZONS:
        raise HistoricalScreeningError(f"Unsupported holding horizon: {horizon}")
    side_fee = round_trip_bps / 20_000.0
    selected_events = [
        event for event in events
        if included_families is None or event.family in included_families
    ]
    by_entry: dict[datetime, list[ReplayEvent]] = defaultdict(list)
    for event in selected_events:
        by_entry[event.entry_hour].append(event)

    cash = INITIAL_EQUITY
    equity_curve: list[float] = [INITIAL_EQUITY]
    realized: list[float] = []
    asset_pnl: dict[str, float] = defaultdict(float)
    family_pnl: dict[str, float] = defaultdict(float)
    entered = 0
    skipped_for_cash = 0

    for block in blocks:
        if len(block) < 2:
            continue
        by_hour = {frame.hour: frame for frame in block}
        open_positions: list[dict[str, Any]] = []
        for frame in block:
            hour = frame.hour
            survivors: list[dict[str, Any]] = []
            for position in open_positions:
                if position["exit_hour"] != hour:
                    survivors.append(position)
                    continue
                price = _spot_mid(frame, position["asset"])
                proceeds = position["quantity"] * price * (1.0 - side_fee)
                pnl = proceeds - position["cash_out"]
                cash += proceeds
                realized.append(pnl)
                asset_pnl[position["asset"]] += pnl
                family_pnl[position["family"]] += pnl
            open_positions = survivors

            mark_before_entries = cash + sum(
                position["quantity"] * _spot_mid(frame, position["asset"])
                for position in open_positions
            )
            entries = sorted(by_entry.get(hour, []), key=lambda item: (item.asset, item.family, item.event_key))
            for event in entries:
                exit_hour = hour + timedelta(hours=horizon)
                if exit_hour not in by_hour:
                    raise HistoricalScreeningError(
                        f"Missing {horizon}h exit for {event.asset} event at {_hour(event.decision_hour)}"
                    )
                desired_cash = mark_before_entries * event.target_weight / horizon
                cash_out = min(cash, desired_cash)
                if cash_out <= 1e-12:
                    skipped_for_cash += 1
                    continue
                price = _spot_mid(frame, event.asset)
                quantity = cash_out / (price * (1.0 + side_fee))
                cash -= cash_out
                open_positions.append({
                    "asset": event.asset,
                    "family": event.family,
                    "quantity": quantity,
                    "cash_out": cash_out,
                    "exit_hour": exit_hour,
                })
                entered += 1

            marked = cash + sum(
                position["quantity"] * _spot_mid(frame, position["asset"])
                for position in open_positions
            )
            equity_curve.append(marked)
        if open_positions:
            raise HistoricalScreeningError("A historical replay block ended with unclosed cohorts")

    final_equity = cash
    wins = sum(pnl > 0.0 for pnl in realized)
    return {
        "horizon_hours": horizon,
        "round_trip_bps": round_trip_bps,
        "initial_equity": INITIAL_EQUITY,
        "final_equity": final_equity,
        "net_return": final_equity / INITIAL_EQUITY - 1.0,
        "maximum_drawdown": _max_drawdown(equity_curve),
        "entered_cohorts": entered,
        "realized_cohorts": len(realized),
        "cash_limited_skips": skipped_for_cash,
        "win_rate": 0.0 if not realized else wins / len(realized),
        "active_day_count": len({event.decision_hour.date().isoformat() for event in selected_events}),
        "realized_pnl_by_asset": dict(sorted(asset_pnl.items())),
        "realized_pnl_by_family": dict(sorted(family_pnl.items())),
    }


def _passive_benchmark(
    frames: list[SnapshotFrame],
    events: list[ReplayEvent],
    *,
    assets: tuple[str, ...],
    round_trip_bps: float,
) -> dict[str, Any]:
    if not events:
        return {"net_return": 0.0, "reason": "no_replayed_events"}
    by_hour = {frame.hour: frame for frame in frames}
    start = min(event.entry_hour for event in events)
    end = max(event.entry_hour + timedelta(hours=8) for event in events)
    if start not in by_hour or end not in by_hour:
        return {"net_return": None, "reason": "benchmark_boundary_missing"}
    side_fee = round_trip_bps / 20_000.0
    allocation = 0.30 / len(assets)
    ending = 0.70
    for asset in assets:
        entry = _spot_mid(by_hour[start], asset)
        exit_price = _spot_mid(by_hour[end], asset)
        ending += allocation * (1.0 - side_fee) * (exit_price / entry) * (1.0 - side_fee)
    return {
        "start_hour_utc": _hour(start),
        "end_hour_utc": _hour(end),
        "round_trip_bps": round_trip_bps,
        "net_return": ending - 1.0,
    }


def evaluate_historical_screening(frames: list[SnapshotFrame]) -> dict[str, Any]:
    canonical = _canonical_frames(frames)
    blocks = _continuous_blocks(canonical)
    events, replay_diagnostics = _replay_decisions(blocks)
    sufficient_blocks = [block for block in blocks if len(block) >= 178]

    if not sufficient_blocks:
        status = "INSUFFICIENT_HISTORICAL_DATA"
        combined: dict[str, Any] = {}
        family_isolated: dict[str, Any] = {}
        leave_one_out: dict[str, Any] = {}
        benchmarks: dict[str, Any] = {}
    else:
        combined = {}
        for horizon in HORIZONS:
            combined[f"h{horizon}_standard"] = _simulate(
                sufficient_blocks,
                events,
                horizon=horizon,
                round_trip_bps=STANDARD_ROUND_TRIP_BPS,
            )
            combined[f"h{horizon}_stress"] = _simulate(
                sufficient_blocks,
                events,
                horizon=horizon,
                round_trip_bps=STRESS_ROUND_TRIP_BPS,
            )
        family_isolated = {
            family: {
                "standard": _simulate(
                    sufficient_blocks,
                    events,
                    horizon=4,
                    round_trip_bps=STANDARD_ROUND_TRIP_BPS,
                    included_families={family},
                ),
                "stress": _simulate(
                    sufficient_blocks,
                    events,
                    horizon=4,
                    round_trip_bps=STRESS_ROUND_TRIP_BPS,
                    included_families={family},
                ),
            }
            for family in FAMILIES
        }
        leave_one_out = {
            family: _simulate(
                sufficient_blocks,
                events,
                horizon=4,
                round_trip_bps=STANDARD_ROUND_TRIP_BPS,
                included_families=set(FAMILIES) - {family},
            )
            for family in FAMILIES
        }
        all_usable_frames = [frame for block in sufficient_blocks for frame in block]
        benchmarks = {
            "cash": {"net_return": 0.0},
            "btc_30pct_standard": _passive_benchmark(
                all_usable_frames,
                events,
                assets=("BTC",),
                round_trip_bps=STANDARD_ROUND_TRIP_BPS,
            ),
            "equal_weight_30pct_standard": _passive_benchmark(
                all_usable_frames,
                events,
                assets=tuple(v25.ASSETS),
                round_trip_bps=STANDARD_ROUND_TRIP_BPS,
            ),
        }
        primary = combined["h4_standard"]
        status = (
            "HISTORICAL_SCREENING_COMPLETE"
            if primary["entered_cohorts"] > 0 and primary["net_return"] > 0.0
            else "HISTORICAL_SCREENING_REJECTED"
        )

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": "HISTORICAL_SCREENING_ONLY",
        "status": status,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "forward_proof": False,
        "eligible_for_promotion": False,
        "historical_outcomes_may_not_modify_v25": True,
        "canonical_snapshot_count": len(canonical),
        "continuous_block_lengths": [len(block) for block in blocks],
        "sufficient_block_count": len(sufficient_blocks),
        "minimum_required_snapshots": 178,
        "event_count": len(events),
        "replay_diagnostics": replay_diagnostics,
        "combined_results": combined,
        "family_isolated_results": family_isolated,
        "leave_one_family_out_results": leave_one_out,
        "benchmarks": benchmarks,
        "fingerprints": _fingerprints(),
    }
    report["report_sha256"] = hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run v2.5 historical screening without changing Track A.")
    parser.add_argument("--folder", required=True, help="Folder of normalized v2.0-compatible snapshots")
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args(argv)
    report = evaluate_historical_screening(load_forward_snapshots(args.folder))
    _atomic_json(Path(args.json_out), report)
    primary = report["combined_results"].get("h4_standard")
    print(json.dumps({
        "status": report["status"],
        "event_count": report["event_count"],
        "primary_four_hour_standard": primary,
        "forward_proof": False,
        "eligible_for_promotion": False,
        "authorizes_trading": False,
        "report_sha256": report["report_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
