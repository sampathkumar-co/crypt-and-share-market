from __future__ import annotations

from tradebot.research import dual_source_consensus_v631 as v631


def test_artifact_verified_v62_fingerprint_is_active():
    assert v631.ARTIFACT_VERIFIED_V62_SHA256 == (
        "7763dfbb68441e496ee638e23e7bd2650bf433a0d63a332d3b02c94709b60d7e"
    )
    assert v631.base.EXPECTED_V62_SHA256 == v631.ARTIFACT_VERIFIED_V62_SHA256
    assert v631.CORRECTION_PATH.is_file()


def test_artifact_report_wrapper_delegates_once(monkeypatch):
    calls = []

    def original(*args, **kwargs):
        calls.append((args, kwargs))
        return {
            "paper_only": True,
            "authorizes_trading": False,
            "authorizes_continuous_paper": False,
            "status": "DUAL_SOURCE_CONSENSUS_REJECTED",
            "report_sha256": "superseded-by-wrapper",
        }

    monkeypatch.setattr(v631, "_ORIGINAL_BUILD_REPORT", original)
    report = v631.build_report("a", key="b")
    assert len(calls) == 1
    assert report["authoritative_v62_report_sha256"] == (
        v631.ARTIFACT_VERIFIED_V62_SHA256
    )
    assert report["schema_version"] == (
        "6.3.1-dual-source-consensus-artifact-corrected"
    )
    assert len(report["report_sha256"]) == 64
