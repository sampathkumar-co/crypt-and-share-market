from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Mapping


@dataclass(frozen=True)
class SealedPrediction:
    candidate_id: str
    decision_time: datetime
    horizon_end: datetime
    weights: Mapping[str, float]
    cash_weight: float
    model_fingerprint: str
    data_fingerprint: str
    authorizes_trading: bool = False

    def __post_init__(self) -> None:
        if self.decision_time.tzinfo is None or self.horizon_end.tzinfo is None:
            raise ValueError("prediction times must be timezone-aware")
        if self.horizon_end <= self.decision_time:
            raise ValueError("horizon_end must follow decision_time")
        if self.authorizes_trading:
            raise ValueError("v5 predictions must remain paper-only")
        if any(value < 0 or value > 0.05 for value in self.weights.values()):
            raise ValueError("asset weight outside [0, 5%]")
        if sum(self.weights.values()) > 0.10 + 1e-12:
            raise ValueError("total exposure exceeds 10%")
        if abs(self.cash_weight + sum(self.weights.values()) - 1.0) > 1e-9:
            raise ValueError("weights and cash must sum to one")
        if self.cash_weight < 0.90 - 1e-12:
            raise ValueError("cash weight must be at least 90%")

    def canonical_payload(self) -> str:
        payload = asdict(self)
        payload["decision_time"] = self.decision_time.astimezone(timezone.utc).isoformat()
        payload["horizon_end"] = self.horizon_end.astimezone(timezone.utc).isoformat()
        payload["weights"] = dict(sorted(self.weights.items()))
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @property
    def seal(self) -> str:
        return sha256(self.canonical_payload().encode()).hexdigest()


def verify_seal(prediction: SealedPrediction, expected_seal: str) -> bool:
    return prediction.seal == expected_seal
