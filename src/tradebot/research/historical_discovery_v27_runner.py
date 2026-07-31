from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import historical_discovery_v26 as v26
from tradebot.research import historical_discovery_v27 as v27


def run_guarded_discovery(max_workers: int = 20) -> dict[str, object]:
    """Run v2.7 with its protocol-frozen ten-day state-assembly warm-up."""
    v26.WARMUP_HOURS = v27.WARMUP_HOURS
    if v26.WARMUP_HOURS != 10 * 24:
        raise RuntimeError("v2.7 effective warm-up must be exactly 240 hours")
    report = v27.run_discovery(max_workers=max_workers)
    report["effective_state_assembly_warmup_hours"] = v26.WARMUP_HOURS
    fingerprints = dict(report["fingerprints"])
    fingerprints["runtime_guard_sha256"] = hashlib.sha256(
        Path(__file__).resolve().read_bytes()
    ).hexdigest()
    report["fingerprints"] = fingerprints
    report.pop("report_sha256", None)
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run v2.7 with the frozen ten-day state-assembly warm-up."
    )
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--max-workers", type=int, default=20)
    args = parser.parse_args(argv)
    report = run_guarded_discovery(max_workers=args.max_workers)
    _atomic_json(Path(args.json_out), report)
    primary = report["results"]["8"]["standard"]
    print(
        json.dumps(
            {
                "status": report["screening_status"],
                "events": report["event_count"],
                "validation_events": report["validation_event_count"],
                "validation_returns": {
                    name: primary["window_returns"][name]
                    for name in v27.VALIDATION_WINDOWS
                },
                "eight_hour_net_return": primary["net_compounded_return"],
                "eight_hour_stress_return": report["results"]["8"]["stress"]["net_compounded_return"],
                "effective_state_assembly_warmup_hours": report[
                    "effective_state_assembly_warmup_hours"
                ],
                "report_sha256": report["report_sha256"],
                "paper_only": True,
                "authorizes_trading": False,
                "authorizes_shadow_paper": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
