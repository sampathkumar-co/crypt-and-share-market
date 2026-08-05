from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Sequence

from .tournament import CandidateEvidence, rank_candidates


def build_tournament_report(candidates: Sequence[CandidateEvidence]) -> dict:
    ranking = rank_candidates(candidates)
    rows = []
    for candidate_id, score, decision in ranking:
        rows.append(
            {
                "candidate_id": candidate_id,
                "score": score,
                "status": decision.status,
                "passed": decision.passed,
                "failures": list(decision.failures),
                "standard": asdict(decision.standard),
                "stressed": asdict(decision.stressed),
                "evidence_fingerprint": decision.evidence_fingerprint,
            }
        )
    champion = next((row["candidate_id"] for row in rows if row["passed"]), None)
    payload = {
        "schema_version": "v5.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "paper_only": True,
        "authorizes_trading": False,
        "champion": champion,
        "ranking": rows,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["report_fingerprint"] = sha256(canonical.encode()).hexdigest()
    return payload


def write_report(path: str | Path, report: dict) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"refusing to replace existing report: {destination}")
    destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
