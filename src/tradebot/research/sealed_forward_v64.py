from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping


PROTOCOL_PATH = Path("research/V64_SEALED_FORWARD_PROTOCOL.md")
CANDIDATE_REPORT_SHA256 = "53642b99bb659fa8eabc86474ebc205742670d731f0f3f2eca6be50275459f1a"
FIRST_ELIGIBLE_DATE = date(2026, 8, 6)
ASSETS = ("BTC", "ETH")
MAXIMUM_CRYPTO_EXPOSURE = 0.10
STANDARD_ROUND_TRIP_COST = 0.0020
STRESS_ROUND_TRIP_COST = 0.0040


class SealedForwardV64Error(RuntimeError):
    """Raised when the prospective evidence contract is violated."""


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def protocol_sha256() -> str:
    if not PROTOCOL_PATH.is_file():
        raise SealedForwardV64Error("v6.4 protocol is missing")
    return sha256(PROTOCOL_PATH.read_bytes()).hexdigest()


def _validate_digest(name: str, value: str) -> None:
    if len(value) != 64:
        raise SealedForwardV64Error(f"{name} must be a SHA-256 digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise SealedForwardV64Error(f"{name} must be hexadecimal") from exc


def _normalized_target(target: Mapping[str, float]) -> dict[str, float]:
    unknown = set(target) - set(ASSETS)
    if unknown:
        raise SealedForwardV64Error(f"unsupported assets: {sorted(unknown)}")
    result: dict[str, float] = {}
    for asset in ASSETS:
        value = float(target.get(asset, 0.0))
        if value < -1e-15:
            raise SealedForwardV64Error("negative target weight")
        if value > 1e-15:
            result[asset] = value
    if sum(result.values()) > MAXIMUM_CRYPTO_EXPOSURE + 1e-12:
        raise SealedForwardV64Error("maximum crypto exposure exceeded")
    return result


def dual_source_target(
    binance_target: Mapping[str, float], coinbase_target: Mapping[str, float]
) -> dict[str, float]:
    left = _normalized_target(binance_target)
    right = _normalized_target(coinbase_target)
    return {
        asset: min(left.get(asset, 0.0), right.get(asset, 0.0))
        for asset in ASSETS
        if min(left.get(asset, 0.0), right.get(asset, 0.0)) > 1e-15
    }


@dataclass(frozen=True)
class SealedForwardPrediction:
    decision_date: str
    created_at: str
    earliest_executable_at: str
    horizon_end_at: str
    candidate_report_sha256: str
    protocol_sha256: str
    implementation_sha256: str
    binance_data_sha256: str
    coinbase_data_sha256: str
    binance_target: Mapping[str, float]
    coinbase_target: Mapping[str, float]
    final_target: Mapping[str, float]
    cash_weight: float
    genuine_decision: bool
    reason: str
    previous_record_sha256: str | None = None
    paper_only: bool = True
    authorizes_trading: bool = False
    authorizes_continuous_paper: bool = False
    schema_version: str = "6.4-sealed-forward-prediction"

    def __post_init__(self) -> None:
        try:
            decision = date.fromisoformat(self.decision_date)
            created = datetime.fromisoformat(self.created_at)
            executable = datetime.fromisoformat(self.earliest_executable_at)
            horizon_end = datetime.fromisoformat(self.horizon_end_at)
        except ValueError as exc:
            raise SealedForwardV64Error("invalid prediction timestamp") from exc
        if decision < FIRST_ELIGIBLE_DATE:
            raise SealedForwardV64Error("pre-programme dates cannot be sealed")
        for moment in (created, executable, horizon_end):
            if moment.tzinfo is None or moment.utcoffset() != timezone.utc.utcoffset(moment):
                raise SealedForwardV64Error("timestamps must be UTC aware")
        if created >= executable:
            raise SealedForwardV64Error("prediction must precede executable time")
        if executable >= horizon_end:
            raise SealedForwardV64Error("horizon must end after execution")
        if self.candidate_report_sha256 != CANDIDATE_REPORT_SHA256:
            raise SealedForwardV64Error("candidate identity changed")
        for name in (
            "protocol_sha256",
            "implementation_sha256",
            "binance_data_sha256",
            "coinbase_data_sha256",
        ):
            _validate_digest(name, str(getattr(self, name)))
        if self.previous_record_sha256 is not None:
            _validate_digest("previous_record_sha256", self.previous_record_sha256)
        left = _normalized_target(self.binance_target)
        right = _normalized_target(self.coinbase_target)
        final = _normalized_target(self.final_target)
        if final != dual_source_target(left, right):
            raise SealedForwardV64Error("final target is not frozen dual-source minimum")
        expected_cash = 1.0 - sum(final.values())
        if abs(float(self.cash_weight) - expected_cash) > 1e-12:
            raise SealedForwardV64Error("cash weight does not balance target")
        if not self.paper_only or self.authorizes_trading or self.authorizes_continuous_paper:
            raise SealedForwardV64Error("paper-only authorization boundary violated")
        if not self.reason:
            raise SealedForwardV64Error("decision reason is required")

    def payload(self) -> dict[str, object]:
        return asdict(self)

    @property
    def record_sha256(self) -> str:
        return sha256(canonical_json(self.payload()).encode("utf-8")).hexdigest()


def verify_prediction(record: Mapping[str, object], claimed_sha256: str) -> bool:
    _validate_digest("claimed_sha256", claimed_sha256)
    prediction = SealedForwardPrediction(**record)
    return prediction.record_sha256 == claimed_sha256


def append_prediction(directory: Path, prediction: SealedForwardPrediction) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{prediction.decision_date}.json"
    if path.exists():
        raise SealedForwardV64Error("duplicate decision date is forbidden")
    existing = sorted(directory.glob("*.json"))
    actual_previous = None
    if existing:
        prior = json.loads(existing[-1].read_text(encoding="utf-8"))
        actual_previous = str(prior.get("record_sha256", ""))
        _validate_digest("stored previous record", actual_previous)
    if prediction.previous_record_sha256 != actual_previous:
        raise SealedForwardV64Error("previous-record chain does not match")
    envelope = {
        "prediction": prediction.payload(),
        "record_sha256": prediction.record_sha256,
    }
    path.write_text(canonical_json(envelope) + "\n", encoding="utf-8")
    return path
