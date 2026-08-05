from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def fingerprint(value: Any) -> str:
    """Return a deterministic SHA-256 fingerprint for JSON-compatible evidence."""
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DataManifest:
    source: str
    retrieval_time_utc: str
    available_at_utc: str
    raw_sha256: str
    normalized_sha256: str
    rows: int
    missing_rows: int = 0
    duplicate_rows: int = 0

    def validate(self) -> None:
        if not self.source:
            raise ValueError("source is required")
        if self.rows <= 0:
            raise ValueError("rows must be positive")
        if self.missing_rows or self.duplicate_rows:
            raise ValueError("data manifest fails closed on missing or duplicate rows")
        for name in ("raw_sha256", "normalized_sha256"):
            value = getattr(self, name)
            if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        available = datetime.fromisoformat(self.available_at_utc.replace("Z", "+00:00"))
        retrieved = datetime.fromisoformat(self.retrieval_time_utc.replace("Z", "+00:00"))
        if available.tzinfo is None or retrieved.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        if available > retrieved:
            raise ValueError("data cannot be retrieved before it is available")


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    parent_ids: tuple[str, ...]
    hypothesis: str
    code_commit: str
    config: Mapping[str, Any]
    data_manifests: tuple[DataManifest, ...]
    test_intervals: tuple[str, ...]
    paper_only: bool = True
    authorizes_trading: bool = False

    def validate(self) -> None:
        if not self.experiment_id or not self.hypothesis:
            raise ValueError("experiment_id and hypothesis are required")
        if not self.paper_only or self.authorizes_trading:
            raise ValueError("v5 experiments are strictly paper-only")
        if len(self.code_commit) < 7:
            raise ValueError("code_commit is invalid")
        if not self.data_manifests:
            raise ValueError("at least one data manifest is required")
        for manifest in self.data_manifests:
            manifest.validate()
        if len(set(self.test_intervals)) != len(self.test_intervals):
            raise ValueError("test intervals must be unique")

    def digest(self) -> str:
        self.validate()
        return fingerprint(asdict(self))


@dataclass(frozen=True)
class ExperimentResult:
    spec_digest: str
    status: str
    metrics: Mapping[str, float]
    failed_gates: tuple[str, ...] = field(default_factory=tuple)
    consumed_test_intervals: tuple[str, ...] = field(default_factory=tuple)
    created_at_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    paper_only: bool = True
    authorizes_trading: bool = False

    def validate(self) -> None:
        if len(self.spec_digest) != 64:
            raise ValueError("spec_digest must be SHA-256")
        if not self.paper_only or self.authorizes_trading:
            raise ValueError("results cannot authorize trading")
        if self.status not in {
            "REJECTED",
            "HISTORICAL_CANDIDATE",
            "FORWARD_PENDING",
            "FORWARD_REJECTED",
            "FORWARD_CANDIDATE",
        }:
            raise ValueError("unknown result status")
        if len(set(self.consumed_test_intervals)) != len(self.consumed_test_intervals):
            raise ValueError("consumed test intervals must be unique")
        if any(not isinstance(v, (int, float)) for v in self.metrics.values()):
            raise ValueError("all metrics must be numeric")

    def digest(self) -> str:
        self.validate()
        return fingerprint(asdict(self))


def append_record(existing_lines: Sequence[str], record: Mapping[str, Any]) -> list[str]:
    """Append one canonical record without permitting mutation of prior lines."""
    canonical = _canonical_json(record)
    parsed = json.loads(canonical)
    if not parsed.get("paper_only", False) or parsed.get("authorizes_trading", True):
        raise ValueError("append-only records must be paper-only and non-trading")
    return [*existing_lines, canonical]
