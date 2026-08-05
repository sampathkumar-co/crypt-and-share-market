from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from tradebot.research import dual_source_consensus_v63 as base


CORRECTION_PATH = Path("research/V631_V62_ARTIFACT_FINGERPRINT_CORRECTION.md")
ARTIFACT_VERIFIED_V62_SHA256 = (
    "7763dfbb68441e496ee638e23e7bd2650bf433a0d63a332d3b02c94709b60d7e"
)


if not CORRECTION_PATH.is_file():
    raise base.DualSourceConsensusV63Error(
        "v6.3.1 dependency-fingerprint correction is missing"
    )

# The first v6.3 attempt failed before outcomes because this expected
# dependency was copied from an erroneous reconstructed summary. The immutable
# v6.2 artifact and a later byte-for-byte replay both prove 7763... is the
# authoritative rejected report fingerprint.
base.EXPECTED_V62_SHA256 = ARTIFACT_VERIFIED_V62_SHA256


def build_report(*args: Any, **kwargs: Any) -> dict[str, Any]:
    report = base.build_report(*args, **kwargs)
    report.pop("report_sha256", None)
    report["schema_version"] = "6.3.1-dual-source-consensus-artifact-corrected"
    report["v62_artifact_fingerprint_correction_sha256"] = hashlib.sha256(
        CORRECTION_PATH.read_bytes()
    ).hexdigest()
    report["authoritative_v62_report_sha256"] = ARTIFACT_VERIFIED_V62_SHA256
    report["report_sha256"] = hashlib.sha256(
        base.canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    original_builder = base.build_report
    base.build_report = build_report
    try:
        return base.main(argv)
    finally:
        base.build_report = original_builder


if __name__ == "__main__":
    raise SystemExit(main())
