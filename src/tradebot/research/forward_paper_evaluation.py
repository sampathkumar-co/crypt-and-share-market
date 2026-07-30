from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "2.2"
SNAPSHOT_SCHEMA_VERSION = "2.0"
ROUTER_SCHEMA_VERSION = "2.1"
ASSETS = ("BTC", "ETH", "SOL", "AVAX", "LINK", "DOGE")
SLEEVES = (
    "capitulation_recovery_proxy",
    "negative_basis_normalization",
    "spot_led_continuation",
)
PROTOCOL_PATH = Path("research/V22_FORWARD_PAPER_EVALUATION_PROTOCOL.md")
ROUTER_SOURCE_SHA256 = "afae56619ede5c6e459f826b6709be3b918c51a7c2fb3321d23305a0cfa17fae"
ROUTER_PROTOCOL_SHA256 = "1c77b0974f784e59e4c7d916db9dae74b5f25ad742f3fc761fca7010d2f5519d"
STARTING_CAPITAL = 100_000.0
BASE_FRICTION = 0.002
STRESS_FRICTION = 0.004
TAX_RATE = 0.312
TDS_RATE = 0.01


class ForwardPaperEvaluationError(RuntimeError):
    """Raised when append-only forward evidence violates the frozen contract."""


@dataclass(frozen=True)
class EvaluationConfig:
    discovery_intervals: int = 2_160
    half_intervals: int = 1_080
    holdout_intervals: int = 720
    max_drawdown: float = 0.12
    min_active_hours: int = 72
    min_entry_events: int = 6
    min_positive_asset_omissions: int = 4
    min_positive_sleeve_omissions: int = 2
    max_sleeve_gain_share: float = 0.70

    def validate(self) -> None:
        if self.discovery_intervals <= 0 or self.holdout_intervals <= 0:
            raise ForwardPaperEvaluationError("Evaluation intervals must be positive")
        if self.half_intervals * 2 != self.discovery_intervals:
            raise ForwardPaperEvaluationError("Discovery halves must exactly partition discovery")


@dataclass(frozen=True)
class FileEvidence:
    path: str
    sha256: str


@dataclass(frozen=True)
class SnapshotPoint:
    hour: datetime
    snapshot_id: str
    record_sha256: str
    mids: dict[str, float]
    file: FileEvidence


@dataclass(frozen=True)
class DecisionPoint:
    hour: datetime
    report_sha256: str
    target_weights: dict[str, float]
    sleeves: dict[str, str]
    input_snapshot_count: int
    input_snapshots: tuple[dict[str, Any], ...]
    file: FileEvidence
    inventory_file: FileEvidence
    inventory: dict[str, Any]


@dataclass(frozen=True)
class ActivationLock:
    activation_decision_hour_utc: str
    activation_fill_hour_utc: str
    discovery_last_decision_hour_utc: str
    discovery_final_mark_hour_utc: str
    holdout_first_decision_hour_utc: str
    holdout_final_mark_hour_utc: str
    activation_decision_report_sha256: str
    activation_decision_file_sha256: str
    activation_next_snapshot_record_sha256: str
    activation_next_snapshot_file_sha256: str
    router_source_sha256: str = ROUTER_SOURCE_SHA256
    router_protocol_sha256: str = ROUTER_PROTOCOL_SHA256
    schema_version: str = "2.2-activation-1"
    paper_only: bool = True
    authorizes_trading: bool = False


@dataclass
class Lot:
    asset: str
    sleeve: str
    quantity: float
    basis: float


@dataclass
class PortfolioState:
    cash: float = STARTING_CAPITAL
    tds_receivable: float = 0.0
    lots: list[Lot] = field(default_factory=list)
    tax_paid: float = 0.0
    fees_and_slippage: float = 0.0
    tds_withheld: float = 0.0
    active_hours: int = 0
    entry_events: int = 0
    transaction_count: int = 0
    sleeve_positive_gains: dict[str, float] = field(default_factory=dict)
    ledger: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class SimulationResult:
    deployable_return: float
    economic_return: float
    worst_drawdown: float
    ending_deployable_value: float
    ending_economic_value: float
    tax_paid: float
    tds_receivable: float
    fees_and_slippage: float
    active_hours: int
    entry_events: int
    transaction_count: int
    sleeve_positive_gains: dict[str, float]
    deployable_equity: tuple[float, ...]
    economic_equity: tuple[float, ...]
    ledger: tuple[dict[str, Any], ...]


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _file_evidence(path: Path, root: Path) -> FileEvidence:
    return FileEvidence(path.relative_to(root).as_posix(), sha256_bytes(path.read_bytes()))


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ForwardPaperEvaluationError(f"{field} must be a non-empty UTC timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ForwardPaperEvaluationError(f"Invalid {field}: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hour(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ForwardPaperEvaluationError(f"{field} is not numeric") from exc
    if not math.isfinite(number):
        raise ForwardPaperEvaluationError(f"{field} is not finite")
    return number


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ForwardPaperEvaluationError(f"Unreadable JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ForwardPaperEvaluationError(f"JSON root must be an object: {path}")
    return payload


def _validate_embedded_hash(payload: dict[str, Any], key: str, label: str) -> str:
    expected = str(payload.get(key, ""))
    unhashed = dict(payload)
    unhashed.pop(key, None)
    actual = sha256_bytes(canonical_json(unhashed).encode("utf-8"))
    if not expected or expected != actual:
        raise ForwardPaperEvaluationError(f"{label} embedded hash mismatch")
    return expected


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _snapshot_filename_hour(path: Path) -> datetime:
    match = re.match(r"^(\d{8}T\d{2})", path.stem)
    if not match:
        raise ForwardPaperEvaluationError(f"Unrecognized snapshot filename: {path.name}")
    return datetime.strptime(match.group(1), "%Y%m%dT%H").replace(tzinfo=timezone.utc)


def _index_hourly_files(folder: Path, *, label: str) -> dict[datetime, Path]:
    grouped: dict[datetime, list[Path]] = {}
    if not folder.exists():
        return {}
    for path in sorted(folder.glob("*.json")):
        hour = _snapshot_filename_hour(path)
        grouped.setdefault(hour, []).append(path)
    indexed: dict[datetime, Path] = {}
    for hour, paths in grouped.items():
        first = paths[0].read_bytes()
        for duplicate in paths[1:]:
            if duplicate.read_bytes() != first:
                raise ForwardPaperEvaluationError(
                    f"Non-identical duplicate {label} records for {_iso(hour)}"
                )
        indexed[hour] = paths[0]
    return indexed


def _validate_snapshot(path: Path, root: Path) -> SnapshotPoint:
    payload = _read_json(path)
    if payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ForwardPaperEvaluationError(f"Unsupported snapshot schema: {path}")
    if payload.get("paper_only") is not True or payload.get("authorizes_trading") is not False:
        raise ForwardPaperEvaluationError(f"Unsafe snapshot flags: {path}")
    record_sha = _validate_embedded_hash(payload, "record_sha256", "snapshot")
    hour = _hour(_parse_utc(payload.get("hour_bucket_utc"), "snapshot hour"))
    if hour != _snapshot_filename_hour(path):
        raise ForwardPaperEvaluationError(f"Snapshot filename/hour mismatch: {path}")
    captured = _parse_utc(payload.get("captured_at_utc"), "captured_at_utc")
    if _hour(captured) != hour:
        raise ForwardPaperEvaluationError(f"Snapshot capture/hour mismatch: {path}")
    assets = payload.get("assets")
    if not isinstance(assets, dict) or set(assets) != set(ASSETS):
        raise ForwardPaperEvaluationError(f"Snapshot assets do not match frozen universe: {path}")
    mids: dict[str, float] = {}
    for asset in ASSETS:
        record = assets.get(asset)
        if not isinstance(record, dict):
            raise ForwardPaperEvaluationError(f"Missing {asset} snapshot record: {path}")
        quote = record.get("spot_quote")
        if not isinstance(quote, dict) or quote.get("available") is not True:
            raise ForwardPaperEvaluationError(f"Missing {asset} spot mid: {path}")
        mid = _number(quote.get("mid"), f"{asset}.spot_mid")
        if mid <= 0:
            raise ForwardPaperEvaluationError(f"Non-positive {asset} spot mid: {path}")
        mids[asset] = mid
    return SnapshotPoint(
        hour=hour,
        snapshot_id=str(payload.get("snapshot_id", "")),
        record_sha256=record_sha,
        mids=mids,
        file=_file_evidence(path, root),
    )


def _validate_router_fingerprints(payload: dict[str, Any]) -> None:
    fingerprints = payload.get("fingerprints")
    if not isinstance(fingerprints, dict):
        raise ForwardPaperEvaluationError("Router decision is missing fingerprints")
    if fingerprints.get("source_sha256") != ROUTER_SOURCE_SHA256:
        raise ForwardPaperEvaluationError("Router source fingerprint mismatch")
    if fingerprints.get("protocol_sha256") != ROUTER_PROTOCOL_SHA256:
        raise ForwardPaperEvaluationError("Router protocol fingerprint mismatch")


def _validate_decision(
    path: Path,
    inventory_path: Path,
    root: Path,
) -> DecisionPoint:
    payload = _read_json(path)
    if payload.get("schema_version") != ROUTER_SCHEMA_VERSION:
        raise ForwardPaperEvaluationError(f"Unsupported router schema: {path}")
    if payload.get("paper_only") is not True or payload.get("authorizes_trading") is not False:
        raise ForwardPaperEvaluationError(f"Unsafe router flags: {path}")
    report_sha = _validate_embedded_hash(payload, "report_sha256", "router report")
    _validate_router_fingerprints(payload)
    hour = _hour(_parse_utc(payload.get("data_cutoff_utc"), "router cutoff"))
    if hour != _snapshot_filename_hour(path):
        raise ForwardPaperEvaluationError(f"Decision filename/hour mismatch: {path}")
    intended = _parse_utc(payload.get("intended_next_cycle_utc"), "intended_next_cycle_utc")
    if intended != hour + timedelta(hours=1):
        raise ForwardPaperEvaluationError(f"Decision next-cycle mismatch: {path}")

    weights_payload = payload.get("target_weights")
    if not isinstance(weights_payload, dict):
        raise ForwardPaperEvaluationError(f"Decision target weights are malformed: {path}")
    weights: dict[str, float] = {}
    for asset, value in weights_payload.items():
        if asset not in ASSETS:
            raise ForwardPaperEvaluationError(f"Unknown target asset {asset}: {path}")
        weight = _number(value, f"{asset}.target_weight")
        if weight < 0 or weight > 0.25 + 1e-12:
            raise ForwardPaperEvaluationError(f"Invalid target weight for {asset}: {path}")
        if weight > 0:
            weights[asset] = weight
    if sum(weights.values()) > 0.50 + 1e-12:
        raise ForwardPaperEvaluationError(f"Decision exceeds total exposure cap: {path}")
    minimum_cash = _number(payload.get("minimum_cash_weight"), "minimum_cash_weight")
    if minimum_cash < 0.50 - 1e-12:
        raise ForwardPaperEvaluationError(f"Decision violates minimum cash reserve: {path}")
    if abs(minimum_cash - (1.0 - sum(weights.values()))) > 1e-9:
        raise ForwardPaperEvaluationError(f"Decision cash/weight mismatch: {path}")

    selected = payload.get("selected_candidates")
    if not isinstance(selected, list):
        raise ForwardPaperEvaluationError(f"Decision selected candidates are malformed: {path}")
    sleeves: dict[str, str] = {}
    for item in selected:
        if not isinstance(item, dict):
            raise ForwardPaperEvaluationError(f"Malformed selected candidate: {path}")
        asset = str(item.get("asset", ""))
        sleeve = str(item.get("sleeve", ""))
        if asset not in weights or sleeve not in SLEEVES or asset in sleeves:
            raise ForwardPaperEvaluationError(f"Invalid selected candidate {item}: {path}")
        sleeves[asset] = sleeve
    if set(sleeves) != set(weights):
        raise ForwardPaperEvaluationError(f"Selected candidates do not match target weights: {path}")

    input_count = int(payload.get("input_snapshot_count", -1))
    input_snapshots = payload.get("input_snapshots")
    if not isinstance(input_snapshots, list) or input_count != len(input_snapshots):
        raise ForwardPaperEvaluationError(f"Decision input snapshot inventory mismatch: {path}")
    inventory = _read_json(inventory_path)
    if inventory.get("forward_data_branch") != "forward-data/v2":
        raise ForwardPaperEvaluationError(f"Decision inventory branch mismatch: {inventory_path}")
    snapshots = inventory.get("snapshots")
    if not isinstance(snapshots, list):
        raise ForwardPaperEvaluationError(f"Decision inventory snapshots are malformed: {inventory_path}")
    inventory_map = {
        str(item.get("snapshot_file")): str(item.get("snapshot_sha256"))
        for item in snapshots
        if isinstance(item, dict)
    }
    for item in input_snapshots:
        if not isinstance(item, dict):
            raise ForwardPaperEvaluationError(f"Malformed router input snapshot: {path}")
        snapshot_id = str(item.get("snapshot_id", ""))
        if f"{snapshot_id}.json" not in inventory_map:
            raise ForwardPaperEvaluationError(
                f"Router input snapshot is absent from decision inventory: {snapshot_id}"
            )
    return DecisionPoint(
        hour=hour,
        report_sha256=report_sha,
        target_weights=weights,
        sleeves=sleeves,
        input_snapshot_count=input_count,
        input_snapshots=tuple(input_snapshots),
        file=_file_evidence(path, root),
        inventory_file=_file_evidence(inventory_path, root),
        inventory=inventory,
    )


def implementation_fingerprints() -> dict[str, str]:
    if not PROTOCOL_PATH.exists():
        raise ForwardPaperEvaluationError(f"Protocol file is missing: {PROTOCOL_PATH}")
    return {
        "evaluator_sha256": sha256_bytes(Path(__file__).resolve().read_bytes()),
        "protocol_sha256": sha256_bytes(PROTOCOL_PATH.read_bytes()),
        "router_source_sha256": ROUTER_SOURCE_SHA256,
        "router_protocol_sha256": ROUTER_PROTOCOL_SHA256,
    }


class ForwardEvidenceStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if not self.root.exists():
            raise ForwardPaperEvaluationError(f"Forward evidence root does not exist: {self.root}")
        self.snapshot_paths = _index_hourly_files(
            self.root / "data/forward-market-state/normalized",
            label="snapshot",
        )
        self.decision_paths = _index_hourly_files(
            self.root / "data/market-state-router/decisions",
            label="decision",
        )
        self.inventory_paths = _index_hourly_files(
            self.root / "data/market-state-router/inventories",
            label="decision inventory",
        )
        self._snapshots: dict[datetime, SnapshotPoint] = {}
        self._decisions: dict[datetime, DecisionPoint] = {}

    def snapshot(self, hour: datetime) -> SnapshotPoint | None:
        target = _hour(hour)
        path = self.snapshot_paths.get(target)
        if path is None:
            return None
        if target not in self._snapshots:
            self._snapshots[target] = _validate_snapshot(path, self.root)
        return self._snapshots[target]

    def decision(self, hour: datetime) -> DecisionPoint | None:
        target = _hour(hour)
        path = self.decision_paths.get(target)
        inventory_path = self.inventory_paths.get(target)
        if path is None and inventory_path is None:
            return None
        if path is None or inventory_path is None:
            raise ForwardPaperEvaluationError(f"Decision/inventory pair is incomplete for {_iso(target)}")
        if target not in self._decisions:
            self._decisions[target] = _validate_decision(path, inventory_path, self.root)
        return self._decisions[target]

    def validate_decision_inputs(self, decision: DecisionPoint) -> None:
        if decision.input_snapshot_count != 169:
            raise ForwardPaperEvaluationError(
                f"Scored decision must contain exactly 169 snapshots: {_iso(decision.hour)}"
            )
        expected_hours = [decision.hour - timedelta(hours=offset) for offset in range(168, -1, -1)]
        actual_hours: list[datetime] = []
        inventory_map = {
            str(item.get("snapshot_file")): str(item.get("snapshot_sha256"))
            for item in decision.inventory.get("snapshots", [])
            if isinstance(item, dict)
        }
        for item in decision.input_snapshots:
            hour = _hour(_parse_utc(item.get("hour"), "router input hour"))
            actual_hours.append(hour)
            snapshot = self.snapshot(hour)
            if snapshot is None:
                raise ForwardPaperEvaluationError(f"Router input snapshot is missing: {_iso(hour)}")
            if item.get("record_sha256") != snapshot.record_sha256:
                raise ForwardPaperEvaluationError(f"Router input record hash mismatch: {_iso(hour)}")
            filename = f"{snapshot.snapshot_id}.json"
            if inventory_map.get(filename) != snapshot.file.sha256:
                raise ForwardPaperEvaluationError(f"Router inventory file hash mismatch: {filename}")
        if actual_hours != expected_hours:
            raise ForwardPaperEvaluationError(
                f"Router input hours are not contiguous through {_iso(decision.hour)}"
            )

    def activation_lock_path(self) -> Path:
        return self.root / "data/forward-paper-v22/activation.json"

    def load_activation_lock(self) -> ActivationLock | None:
        path = self.activation_lock_path()
        if not path.exists():
            return None
        payload = _read_json(path)
        expected = str(payload.pop("activation_sha256", ""))
        actual = sha256_bytes(canonical_json(payload).encode("utf-8"))
        if expected != actual:
            raise ForwardPaperEvaluationError("Activation lock hash mismatch")
        lock = ActivationLock(**payload)
        _validate_activation_lock(lock)
        return lock


def _validate_activation_lock(lock: ActivationLock) -> None:
    if lock.schema_version != "2.2-activation-1":
        raise ForwardPaperEvaluationError("Unsupported activation-lock schema")
    if lock.paper_only is not True or lock.authorizes_trading is not False:
        raise ForwardPaperEvaluationError("Unsafe activation-lock flags")
    if lock.router_source_sha256 != ROUTER_SOURCE_SHA256:
        raise ForwardPaperEvaluationError("Activation router source fingerprint mismatch")
    if lock.router_protocol_sha256 != ROUTER_PROTOCOL_SHA256:
        raise ForwardPaperEvaluationError("Activation router protocol fingerprint mismatch")
    decision_hour = _hour(_parse_utc(lock.activation_decision_hour_utc, "activation decision"))
    fill_hour = _hour(_parse_utc(lock.activation_fill_hour_utc, "activation fill"))
    if fill_hour != decision_hour + timedelta(hours=1):
        raise ForwardPaperEvaluationError("Activation fill is not the next hour")


def _activation_payload(lock: ActivationLock) -> dict[str, Any]:
    payload = asdict(lock)
    payload["activation_sha256"] = sha256_bytes(canonical_json(payload).encode("utf-8"))
    return payload


def _build_activation_lock(
    decision: DecisionPoint,
    next_snapshot: SnapshotPoint,
    config: EvaluationConfig,
) -> ActivationLock:
    start = decision.hour
    discovery_last_decision = start + timedelta(hours=config.discovery_intervals - 1)
    discovery_final_mark = start + timedelta(hours=config.discovery_intervals + 1)
    holdout_first_decision = start + timedelta(hours=config.discovery_intervals)
    holdout_final_mark = start + timedelta(
        hours=config.discovery_intervals + config.holdout_intervals + 1
    )
    return ActivationLock(
        activation_decision_hour_utc=_iso(start),
        activation_fill_hour_utc=_iso(start + timedelta(hours=1)),
        discovery_last_decision_hour_utc=_iso(discovery_last_decision),
        discovery_final_mark_hour_utc=_iso(discovery_final_mark),
        holdout_first_decision_hour_utc=_iso(holdout_first_decision),
        holdout_final_mark_hour_utc=_iso(holdout_final_mark),
        activation_decision_report_sha256=decision.report_sha256,
        activation_decision_file_sha256=decision.file.sha256,
        activation_next_snapshot_record_sha256=next_snapshot.record_sha256,
        activation_next_snapshot_file_sha256=next_snapshot.file.sha256,
    )


def _find_or_validate_activation(
    store: ForwardEvidenceStore,
    config: EvaluationConfig,
) -> tuple[ActivationLock | None, bool]:
    existing = store.load_activation_lock()
    if existing is not None:
        decision_hour = _hour(
            _parse_utc(existing.activation_decision_hour_utc, "activation decision")
        )
        decision = store.decision(decision_hour)
        snapshot = store.snapshot(decision_hour + timedelta(hours=1))
        if decision is None or snapshot is None:
            raise ForwardPaperEvaluationError("Activation-lock evidence is missing")
        store.validate_decision_inputs(decision)
        if decision.report_sha256 != existing.activation_decision_report_sha256:
            raise ForwardPaperEvaluationError("Activation decision report changed")
        if decision.file.sha256 != existing.activation_decision_file_sha256:
            raise ForwardPaperEvaluationError("Activation decision file changed")
        if snapshot.record_sha256 != existing.activation_next_snapshot_record_sha256:
            raise ForwardPaperEvaluationError("Activation snapshot record changed")
        if snapshot.file.sha256 != existing.activation_next_snapshot_file_sha256:
            raise ForwardPaperEvaluationError("Activation snapshot file changed")
        return existing, False

    for hour in sorted(store.decision_paths):
        decision = store.decision(hour)
        if decision is None or decision.input_snapshot_count != 169:
            continue
        next_snapshot = store.snapshot(hour + timedelta(hours=1))
        if next_snapshot is None:
            continue
        store.validate_decision_inputs(decision)
        lock = _build_activation_lock(decision, next_snapshot, config)
        _validate_activation_lock(lock)
        return lock, True
    return None, False


def _report_hash(payload: dict[str, Any]) -> dict[str, Any]:
    finalized = dict(payload)
    finalized["report_sha256"] = sha256_bytes(canonical_json(payload).encode("utf-8"))
    return finalized


def _asset_quantity(state: PortfolioState, asset: str) -> float:
    return sum(lot.quantity for lot in state.lots if lot.asset == asset)


def _asset_value(state: PortfolioState, asset: str, prices: dict[str, float]) -> float:
    return _asset_quantity(state, asset) * prices[asset]


def _deployable_value(state: PortfolioState, prices: dict[str, float]) -> float:
    return state.cash + sum(lot.quantity * prices[lot.asset] for lot in state.lots)


def _economic_value(state: PortfolioState, prices: dict[str, float]) -> float:
    return _deployable_value(state, prices) + state.tds_receivable


def _exposure(state: PortfolioState, prices: dict[str, float]) -> float:
    return sum(lot.quantity * prices[lot.asset] for lot in state.lots)


def _worst_drawdown(values: list[float]) -> float:
    peak = 0.0
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def _sell_asset(
    state: PortfolioState,
    asset: str,
    quantity: float,
    price: float,
    friction: float,
    hour: datetime,
) -> None:
    total_quantity = _asset_quantity(state, asset)
    if quantity <= 1e-15 or total_quantity <= 0:
        return
    quantity = min(quantity, total_quantity)
    fraction = quantity / total_quantity
    survivors: list[Lot] = []
    for lot in state.lots:
        if lot.asset != asset:
            survivors.append(lot)
            continue
        sold_quantity = lot.quantity * fraction
        remaining_quantity = lot.quantity - sold_quantity
        basis_allocated = lot.basis * fraction
        remaining_basis = lot.basis - basis_allocated
        gross = sold_quantity * price
        taxable_gain = max(0.0, gross - basis_allocated)
        tax = taxable_gain * TAX_RATE
        sell_cost = gross * friction
        tds = gross * TDS_RATE
        state.cash += gross - sell_cost - tax - tds
        state.tax_paid += tax
        state.fees_and_slippage += sell_cost
        state.tds_withheld += tds
        state.tds_receivable += tds
        state.sleeve_positive_gains[lot.sleeve] = (
            state.sleeve_positive_gains.get(lot.sleeve, 0.0) + taxable_gain
        )
        state.ledger.append({
            "hour_utc": _iso(hour),
            "side": "SELL",
            "asset": asset,
            "sleeve": lot.sleeve,
            "quantity": sold_quantity,
            "price": price,
            "gross_consideration": gross,
            "allocated_basis": basis_allocated,
            "taxable_gain": taxable_gain,
            "income_tax": tax,
            "execution_cost": sell_cost,
            "tds_withheld": tds,
        })
        if remaining_quantity > 1e-15:
            survivors.append(Lot(asset, lot.sleeve, remaining_quantity, remaining_basis))
    state.lots = survivors
    state.transaction_count += 1


def _buy_asset(
    state: PortfolioState,
    asset: str,
    sleeve: str,
    desired_consideration: float,
    price: float,
    friction: float,
    hour: datetime,
) -> None:
    if desired_consideration <= 1e-12 or state.cash <= 0:
        return
    consideration = min(desired_consideration, state.cash / (1.0 + friction))
    if consideration <= 1e-12:
        return
    buy_cost = consideration * friction
    quantity = consideration / price
    basis = consideration + buy_cost
    state.cash -= basis
    state.fees_and_slippage += buy_cost
    state.lots.append(Lot(asset, sleeve, quantity, basis))
    state.transaction_count += 1
    state.ledger.append({
        "hour_utc": _iso(hour),
        "side": "BUY",
        "asset": asset,
        "sleeve": sleeve,
        "quantity": quantity,
        "price": price,
        "gross_consideration": consideration,
        "allocated_basis": basis,
        "taxable_gain": 0.0,
        "income_tax": 0.0,
        "execution_cost": buy_cost,
        "tds_withheld": 0.0,
    })


def _rebalance(
    state: PortfolioState,
    decision: DecisionPoint,
    prices: dict[str, float],
    friction: float,
    hour: datetime,
    *,
    omitted_asset: str | None = None,
    omitted_sleeve: str | None = None,
) -> None:
    targets = dict(decision.target_weights)
    if omitted_asset is not None:
        targets.pop(omitted_asset, None)
    if omitted_sleeve is not None:
        targets = {
            asset: weight
            for asset, weight in targets.items()
            if decision.sleeves.get(asset) != omitted_sleeve
        }
    before_exposure = _exposure(state, prices)
    marked_equity = _deployable_value(state, prices)
    target_values = {asset: weight * marked_equity for asset, weight in targets.items()}

    for asset in ASSETS:
        current_value = _asset_value(state, asset, prices)
        target_value = target_values.get(asset, 0.0)
        if current_value > target_value + 1e-9:
            _sell_asset(
                state,
                asset,
                (current_value - target_value) / prices[asset],
                prices[asset],
                friction,
                hour,
            )
    for asset in sorted(targets):
        current_value = _asset_value(state, asset, prices)
        desired = target_values[asset] - current_value
        if desired > 1e-9:
            _buy_asset(
                state,
                asset,
                decision.sleeves[asset],
                desired,
                prices[asset],
                friction,
                hour,
            )
    after_exposure = _exposure(state, prices)
    if after_exposure > 1e-9:
        state.active_hours += 1
    if before_exposure <= 1e-9 and after_exposure > 1e-9:
        state.entry_events += 1


def _finish_simulation(
    state: PortfolioState,
    final_prices: dict[str, float],
    friction: float,
    final_hour: datetime,
    deployable_equity: list[float],
    economic_equity: list[float],
) -> SimulationResult:
    for asset in ASSETS:
        quantity = _asset_quantity(state, asset)
        if quantity > 1e-15:
            _sell_asset(state, asset, quantity, final_prices[asset], friction, final_hour)
    deployable_equity.append(state.cash)
    economic_equity.append(state.cash + state.tds_receivable)
    return SimulationResult(
        deployable_return=state.cash / STARTING_CAPITAL - 1.0,
        economic_return=(state.cash + state.tds_receivable) / STARTING_CAPITAL - 1.0,
        worst_drawdown=_worst_drawdown(deployable_equity),
        ending_deployable_value=state.cash,
        ending_economic_value=state.cash + state.tds_receivable,
        tax_paid=state.tax_paid,
        tds_receivable=state.tds_receivable,
        fees_and_slippage=state.fees_and_slippage,
        active_hours=state.active_hours,
        entry_events=state.entry_events,
        transaction_count=state.transaction_count,
        sleeve_positive_gains=dict(sorted(state.sleeve_positive_gains.items())),
        deployable_equity=tuple(deployable_equity),
        economic_equity=tuple(economic_equity),
        ledger=tuple(state.ledger),
    )


def simulate_router_block(
    decisions: list[DecisionPoint],
    snapshots: dict[datetime, SnapshotPoint],
    *,
    friction: float = BASE_FRICTION,
    omitted_asset: str | None = None,
    omitted_sleeve: str | None = None,
) -> SimulationResult:
    if not decisions:
        raise ForwardPaperEvaluationError("Cannot simulate an empty decision block")
    state = PortfolioState()
    deployable_equity = [STARTING_CAPITAL]
    economic_equity = [STARTING_CAPITAL]
    for index, decision in enumerate(decisions):
        if index and decision.hour != decisions[index - 1].hour + timedelta(hours=1):
            raise ForwardPaperEvaluationError("Decision block is not hourly contiguous")
        fill = snapshots.get(decision.hour + timedelta(hours=1))
        mark = snapshots.get(decision.hour + timedelta(hours=2))
        if fill is None or mark is None:
            raise ForwardPaperEvaluationError(
                f"Missing fill or mark snapshot for decision {_iso(decision.hour)}"
            )
        _rebalance(
            state,
            decision,
            fill.mids,
            friction,
            fill.hour,
            omitted_asset=omitted_asset,
            omitted_sleeve=omitted_sleeve,
        )
        deployable_equity.append(_deployable_value(state, mark.mids))
        economic_equity.append(_economic_value(state, mark.mids))
    final_mark = snapshots[decisions[-1].hour + timedelta(hours=2)]
    return _finish_simulation(
        state,
        final_mark.mids,
        friction,
        final_mark.hour,
        deployable_equity,
        economic_equity,
    )


def simulate_equal_weight_benchmark(
    snapshots: dict[datetime, SnapshotPoint],
    *,
    fill_hour: datetime,
    final_mark_hour: datetime,
    friction: float = BASE_FRICTION,
) -> SimulationResult:
    fill = snapshots.get(fill_hour)
    final_mark = snapshots.get(final_mark_hour)
    if fill is None or final_mark is None:
        raise ForwardPaperEvaluationError("Benchmark fill/final snapshot is missing")
    state = PortfolioState()
    budget = STARTING_CAPITAL / len(ASSETS)
    for asset in ASSETS:
        consideration = budget / (1.0 + friction)
        _buy_asset(state, asset, "equal_weight_benchmark", consideration, fill.mids[asset], friction, fill.hour)
    state.entry_events = 1
    state.active_hours = max(0, int((final_mark_hour - fill_hour).total_seconds() // 3600))
    deployable_equity = [STARTING_CAPITAL]
    economic_equity = [STARTING_CAPITAL]
    cursor = fill_hour + timedelta(hours=1)
    while cursor <= final_mark_hour:
        snapshot = snapshots.get(cursor)
        if snapshot is None:
            raise ForwardPaperEvaluationError(f"Benchmark mark is missing: {_iso(cursor)}")
        deployable_equity.append(_deployable_value(state, snapshot.mids))
        economic_equity.append(_economic_value(state, snapshot.mids))
        cursor += timedelta(hours=1)
    return _finish_simulation(
        state,
        final_mark.mids,
        friction,
        final_mark.hour,
        deployable_equity,
        economic_equity,
    )


def _simulation_summary(result: SimulationResult, *, include_ledger: bool = False) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "deployable_return": result.deployable_return,
        "economic_return": result.economic_return,
        "worst_drawdown": result.worst_drawdown,
        "ending_deployable_value": result.ending_deployable_value,
        "ending_economic_value": result.ending_economic_value,
        "tax_paid": result.tax_paid,
        "tds_receivable": result.tds_receivable,
        "fees_and_slippage": result.fees_and_slippage,
        "active_hours": result.active_hours,
        "entry_events": result.entry_events,
        "transaction_count": result.transaction_count,
        "sleeve_positive_gains": result.sleeve_positive_gains,
        "deployable_equity_points": len(result.deployable_equity),
        "economic_equity_points": len(result.economic_equity),
    }
    if include_ledger:
        summary["ledger"] = list(result.ledger)
    return summary


def _required_block_hours(
    start: datetime,
    intervals: int,
) -> tuple[list[datetime], list[datetime]]:
    decision_hours = [start + timedelta(hours=index) for index in range(intervals)]
    snapshot_hours = [start + timedelta(hours=index) for index in range(1, intervals + 2)]
    return decision_hours, snapshot_hours


def _block_availability(
    store: ForwardEvidenceStore,
    start: datetime,
    intervals: int,
) -> dict[str, Any]:
    decision_hours, snapshot_hours = _required_block_hours(start, intervals)
    missing_decisions = [hour for hour in decision_hours if hour not in store.decision_paths]
    missing_snapshots = [hour for hour in snapshot_hours if hour not in store.snapshot_paths]
    contiguous = 0
    for index, decision_hour in enumerate(decision_hours):
        fill_hour = decision_hour + timedelta(hours=1)
        mark_hour = decision_hour + timedelta(hours=2)
        if (
            decision_hour not in store.decision_paths
            or fill_hour not in store.snapshot_paths
            or mark_hour not in store.snapshot_paths
        ):
            break
        contiguous = index + 1
    return {
        "complete": not missing_decisions and not missing_snapshots,
        "available_contiguous_intervals": contiguous,
        "missing_decision_hours": [_iso(hour) for hour in missing_decisions],
        "missing_snapshot_hours": [_iso(hour) for hour in missing_snapshots],
    }


def _load_complete_block(
    store: ForwardEvidenceStore,
    start: datetime,
    intervals: int,
) -> tuple[list[DecisionPoint], dict[datetime, SnapshotPoint]]:
    availability = _block_availability(store, start, intervals)
    if not availability["complete"]:
        raise ForwardPaperEvaluationError("Attempted to load an incomplete scored block")
    decision_hours, snapshot_hours = _required_block_hours(start, intervals)
    decisions: list[DecisionPoint] = []
    for hour in decision_hours:
        decision = store.decision(hour)
        if decision is None:
            raise ForwardPaperEvaluationError(f"Missing decision after completeness check: {_iso(hour)}")
        store.validate_decision_inputs(decision)
        decisions.append(decision)
    snapshots: dict[datetime, SnapshotPoint] = {}
    for hour in snapshot_hours:
        snapshot = store.snapshot(hour)
        if snapshot is None:
            raise ForwardPaperEvaluationError(f"Missing snapshot after completeness check: {_iso(hour)}")
        snapshots[hour] = snapshot
    return decisions, snapshots


def _used_file_inventory(
    store: ForwardEvidenceStore,
    *,
    forward_data_head: str,
    activation: ActivationLock | None,
) -> dict[str, Any]:
    evidence: dict[str, FileEvidence] = {}
    for snapshot in store._snapshots.values():
        evidence[snapshot.file.path] = snapshot.file
    for decision in store._decisions.values():
        evidence[decision.file.path] = decision.file
        evidence[decision.inventory_file.path] = decision.inventory_file
    activation_path = store.activation_lock_path()
    if activation is not None and activation_path.exists():
        item = _file_evidence(activation_path, store.root)
        evidence[item.path] = item
    files = [asdict(evidence[path]) for path in sorted(evidence)]
    return {
        "forward_data_branch": "forward-data/v2",
        "forward_data_head": forward_data_head,
        "file_count": len(files),
        "files": files,
    }


def _precompletion_report(
    *,
    status: str,
    reason: str,
    forward_data_head: str,
    store: ForwardEvidenceStore,
    activation: ActivationLock | None,
    activation_is_new: bool,
    config: EvaluationConfig,
    availability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "status": status,
        "reason": reason,
        "evaluation_config": asdict(config),
        "activation_locked": activation is not None and not activation_is_new,
        "activation_candidate": None if activation is None else _activation_payload(activation),
        "available_contiguous_intervals": 0 if availability is None else availability["available_contiguous_intervals"],
        "missing_decision_hours": [] if availability is None else availability["missing_decision_hours"],
        "missing_snapshot_hours": [] if availability is None else availability["missing_snapshot_hours"],
        "input_inventory": _used_file_inventory(
            store,
            forward_data_head=forward_data_head,
            activation=activation,
        ),
        "fingerprints": implementation_fingerprints(),
    }
    return _report_hash(report)


def _discovery_metrics(
    decisions: list[DecisionPoint],
    snapshots: dict[datetime, SnapshotPoint],
    config: EvaluationConfig,
) -> dict[str, Any]:
    base = simulate_router_block(decisions, snapshots, friction=BASE_FRICTION)
    stress = simulate_router_block(decisions, snapshots, friction=STRESS_FRICTION)
    first = simulate_router_block(
        decisions[: config.half_intervals],
        snapshots,
        friction=BASE_FRICTION,
    )
    second = simulate_router_block(
        decisions[config.half_intervals :],
        snapshots,
        friction=BASE_FRICTION,
    )
    fill_hour = decisions[0].hour + timedelta(hours=1)
    final_mark_hour = decisions[-1].hour + timedelta(hours=2)
    benchmark = simulate_equal_weight_benchmark(
        snapshots,
        fill_hour=fill_hour,
        final_mark_hour=final_mark_hour,
        friction=BASE_FRICTION,
    )

    asset_omissions = {
        asset: simulate_router_block(
            decisions,
            snapshots,
            friction=BASE_FRICTION,
            omitted_asset=asset,
        )
        for asset in ASSETS
    }
    sleeve_omissions = {
        sleeve: simulate_router_block(
            decisions,
            snapshots,
            friction=BASE_FRICTION,
            omitted_sleeve=sleeve,
        )
        for sleeve in SLEEVES
    }
    positive_asset_omissions = sum(
        result.deployable_return > 0 for result in asset_omissions.values()
    )
    positive_sleeve_omissions = sum(
        result.deployable_return > 0 for result in sleeve_omissions.values()
    )
    gains = base.sleeve_positive_gains
    total_positive_gains = sum(gains.values())
    maximum_sleeve_gain_share = (
        None if total_positive_gains <= 0 else max(gains.values(), default=0.0) / total_positive_gains
    )

    checks = {
        "positive_deployable_return": base.deployable_return > 0,
        "positive_economic_return": base.economic_return > 0,
        "positive_first_half": first.deployable_return > 0,
        "positive_second_half": second.deployable_return > 0,
        "positive_double_cost": stress.deployable_return > 0,
        "beats_equal_weight_benchmark": base.deployable_return > benchmark.deployable_return,
        "drawdown_within_limit": base.worst_drawdown <= config.max_drawdown,
        "enough_active_hours": base.active_hours >= config.min_active_hours,
        "enough_entry_events": base.entry_events >= config.min_entry_events,
        "asset_omission_robustness": (
            positive_asset_omissions >= config.min_positive_asset_omissions
        ),
        "sleeve_omission_robustness": (
            positive_sleeve_omissions >= config.min_positive_sleeve_omissions
        ),
        "sleeve_gain_concentration": (
            maximum_sleeve_gain_share is not None
            and maximum_sleeve_gain_share <= config.max_sleeve_gain_share
        ),
    }
    reasons = [name for name, passed in checks.items() if not passed]
    return {
        "passed": not reasons,
        "rejection_reasons": reasons,
        "checks": checks,
        "base": _simulation_summary(base, include_ledger=True),
        "double_cost": _simulation_summary(stress, include_ledger=True),
        "first_half": _simulation_summary(first),
        "second_half": _simulation_summary(second),
        "cash_benchmark": {"deployable_return": 0.0, "economic_return": 0.0},
        "equal_weight_benchmark": _simulation_summary(benchmark),
        "leave_one_asset_out": {
            asset: _simulation_summary(result)
            for asset, result in sorted(asset_omissions.items())
        },
        "leave_one_sleeve_out": {
            sleeve: _simulation_summary(result)
            for sleeve, result in sorted(sleeve_omissions.items())
        },
        "positive_asset_omissions": positive_asset_omissions,
        "positive_sleeve_omissions": positive_sleeve_omissions,
        "maximum_sleeve_positive_gain_share": maximum_sleeve_gain_share,
    }


def _holdout_metrics(
    decisions: list[DecisionPoint],
    snapshots: dict[datetime, SnapshotPoint],
    config: EvaluationConfig,
) -> dict[str, Any]:
    base = simulate_router_block(decisions, snapshots, friction=BASE_FRICTION)
    stress = simulate_router_block(decisions, snapshots, friction=STRESS_FRICTION)
    fill_hour = decisions[0].hour + timedelta(hours=1)
    final_mark_hour = decisions[-1].hour + timedelta(hours=2)
    benchmark = simulate_equal_weight_benchmark(
        snapshots,
        fill_hour=fill_hour,
        final_mark_hour=final_mark_hour,
        friction=BASE_FRICTION,
    )
    checks = {
        "positive_deployable_return": base.deployable_return > 0,
        "positive_double_cost": stress.deployable_return > 0,
        "beats_cash": base.deployable_return > 0,
        "beats_equal_weight_benchmark": base.deployable_return > benchmark.deployable_return,
        "drawdown_within_limit": base.worst_drawdown <= config.max_drawdown,
        "enough_entry_events": base.entry_events >= 2,
    }
    reasons = [name for name, passed in checks.items() if not passed]
    return {
        "passed": not reasons,
        "rejection_reasons": reasons,
        "checks": checks,
        "base": _simulation_summary(base, include_ledger=True),
        "double_cost": _simulation_summary(stress, include_ledger=True),
        "cash_benchmark": {"deployable_return": 0.0, "economic_return": 0.0},
        "equal_weight_benchmark": _simulation_summary(benchmark),
    }


def evaluate_forward_paper(
    store_root: str | Path,
    *,
    forward_data_head: str,
    config: EvaluationConfig | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    cfg = config or EvaluationConfig()
    cfg.validate()
    if not re.fullmatch(r"[0-9a-f]{40}", forward_data_head):
        raise ForwardPaperEvaluationError("forward_data_head must be a full commit SHA")
    store = ForwardEvidenceStore(store_root)
    activation, activation_is_new = _find_or_validate_activation(store, cfg)
    if activation is None:
        report = _precompletion_report(
            status="WAITING_FOR_ACTIVATION",
            reason="no_router_decision_with_169_contiguous_snapshots_and_next_hour_price",
            forward_data_head=forward_data_head,
            store=store,
            activation=None,
            activation_is_new=False,
            config=cfg,
        )
        report["available_snapshot_files"] = len(store.snapshot_paths)
        report["available_router_decision_files"] = len(store.decision_paths)
        return _report_hash({key: value for key, value in report.items() if key != "report_sha256"}), None

    activation_payload = _activation_payload(activation)
    discovery_start = _hour(
        _parse_utc(activation.activation_decision_hour_utc, "activation decision")
    )
    discovery_availability = _block_availability(
        store,
        discovery_start,
        cfg.discovery_intervals,
    )
    if not discovery_availability["complete"]:
        report = _precompletion_report(
            status="COLLECTING_DISCOVERY",
            reason="complete_2160_interval_discovery_block_not_available",
            forward_data_head=forward_data_head,
            store=store,
            activation=activation,
            activation_is_new=activation_is_new,
            config=cfg,
            availability=discovery_availability,
        )
        return report, activation_payload if activation_is_new else None
    discovery_decisions, discovery_snapshots = _load_complete_block(
        store,
        discovery_start,
        cfg.discovery_intervals,
    )
    discovery = _discovery_metrics(discovery_decisions, discovery_snapshots, cfg)
    boundaries = {
        "activation_decision_hour_utc": activation.activation_decision_hour_utc,
        "activation_fill_hour_utc": activation.activation_fill_hour_utc,
        "discovery_last_decision_hour_utc": activation.discovery_last_decision_hour_utc,
        "discovery_final_mark_hour_utc": activation.discovery_final_mark_hour_utc,
        "holdout_first_decision_hour_utc": activation.holdout_first_decision_hour_utc,
        "holdout_final_mark_hour_utc": activation.holdout_final_mark_hour_utc,
    }
    base_report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "eligible_for_shadow_paper_review": False,
        "activation": activation_payload,
        "evaluation_config": asdict(cfg),
        "boundaries": boundaries,
        "discovery": discovery,
        "fingerprints": implementation_fingerprints(),
    }
    if not discovery["passed"]:
        base_report.update({
            "status": "DISCOVERY_REJECTED",
            "reason": "one_or_more_frozen_discovery_gates_failed",
            "holdout_accessed": False,
            "input_inventory": _used_file_inventory(
                store,
                forward_data_head=forward_data_head,
                activation=activation,
            ),
        })
        return _report_hash(base_report), activation_payload if activation_is_new else None

    holdout_start = _hour(
        _parse_utc(activation.holdout_first_decision_hour_utc, "holdout first decision")
    )
    holdout_availability = _block_availability(
        store,
        holdout_start,
        cfg.holdout_intervals,
    )
    if not holdout_availability["complete"]:
        base_report.update({
            "status": "DISCOVERY_PASSED_COLLECTING_HOLDOUT",
            "reason": "complete_720_interval_promotion_holdout_not_available",
            "holdout_accessed": False,
            "holdout_availability": holdout_availability,
            "input_inventory": _used_file_inventory(
                store,
                forward_data_head=forward_data_head,
                activation=activation,
            ),
        })
        return _report_hash(base_report), activation_payload if activation_is_new else None

    holdout_decisions, holdout_snapshots = _load_complete_block(
        store,
        holdout_start,
        cfg.holdout_intervals,
    )
    holdout = _holdout_metrics(holdout_decisions, holdout_snapshots, cfg)
    base_report.update({
        "status": "HOLDOUT_PASSED" if holdout["passed"] else "HOLDOUT_REJECTED",
        "reason": (
            "eligible_for_separate_time_limited_shadow_paper_review"
            if holdout["passed"]
            else "one_or_more_frozen_holdout_gates_failed"
        ),
        "holdout_accessed": True,
        "eligible_for_shadow_paper_review": bool(holdout["passed"]),
        "holdout": holdout,
        "input_inventory": _used_file_inventory(
            store,
            forward_data_head=forward_data_head,
            activation=activation,
        ),
    })
    return _report_hash(base_report), activation_payload if activation_is_new else None



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the frozen future-only v2.2 paper research block."
    )
    parser.add_argument("--store-root", required=True)
    parser.add_argument("--forward-data-head", required=True)
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--activation-out")
    args = parser.parse_args(argv)

    report, new_activation = evaluate_forward_paper(
        args.store_root,
        forward_data_head=args.forward_data_head,
    )
    _atomic_json(Path(args.json_out), report)
    if new_activation is not None and args.activation_out:
        _atomic_json(Path(args.activation_out), new_activation)
    print(json.dumps({
        "status": report["status"],
        "reason": report["reason"],
        "authorizes_trading": False,
        "authorizes_shadow_paper": False,
        "eligible_for_shadow_paper_review": report.get(
            "eligible_for_shadow_paper_review",
            False,
        ),
        "activation_candidate_created": new_activation is not None,
        "report_sha256": report["report_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
