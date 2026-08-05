from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from tradebot.research.forward_alpha_v25 import canonical_json


EXPECTED_V312_SHA256 = "90dea7bcc12274146f730ba5a5cd9f93179ff944211ff07de849aca68e468c22"
EXPECTED_V32_SHA256 = "c8a2bf7204681cdd5ce642886a42ea361f016008d908cfa16d299798cb9fefc4"
SCHEMA_VERSION = "6.0-common-accounting-champion-screen"


class TournamentV60Error(RuntimeError):
    """Raised when frozen tournament evidence is incomplete or inconsistent."""


@dataclass(frozen=True)
class MaterialGates:
    annualized_return_at_least_5pct: bool
    annualized_excess_over_cash_at_least_2pct: bool
    positive_stress_return: bool
    four_of_five_positive_standard_windows: bool
    three_of_five_positive_stress_windows: bool
    at_least_30_actions: bool
    drawdown_at_most_5pct: bool
    positive_delayed_execution: bool
    trade_concentration_at_most_20pct: bool
    window_concentration_at_most_50pct: bool
    independent_source_linked: bool
    frozen_dependencies_exact: bool

    @property
    def passed(self) -> bool:
        return all(asdict(self).values())


@dataclass(frozen=True)
class ConservativeChampionEvidence:
    strategy_id: str
    years: int
    standard_return: float
    stress_return: float
    cash_return: float
    annualized_standard_return: float
    annualized_cash_return: float
    annualized_excess_over_cash: float
    standard_windows: Mapping[str, float]
    stress_windows: Mapping[str, float]
    actions: int
    maximum_drawdown: float
    maximum_positive_window_share: float
    source_report_sha256: Mapping[str, str]
    delayed_execution_return: float | None = None
    maximum_positive_trade_share: float | None = None


def _validate_report_sha(report: Mapping[str, Any], expected: str, name: str) -> None:
    if report.get("paper_only") is not True:
        raise TournamentV60Error(f"{name} is not paper-only")
    if report.get("authorizes_trading") is not False:
        raise TournamentV60Error(f"{name} authorizes trading")
    payload = dict(report)
    claimed = str(payload.pop("report_sha256", ""))
    computed = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    if claimed != computed:
        raise TournamentV60Error(f"{name} report hash does not match contents")
    if claimed != expected:
        raise TournamentV60Error(
            f"{name} report does not reproduce frozen hash: {claimed} != {expected}"
        )


def _annualize(compounded_return: float, years: int) -> float:
    if years <= 0 or compounded_return <= -1.0:
        raise TournamentV60Error("invalid annualization inputs")
    return (1.0 + compounded_return) ** (1.0 / years) - 1.0


def _minimum_windows(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> dict[str, float]:
    if set(left) != set(right):
        raise TournamentV60Error("independent-source windows do not align")
    return {
        key: min(float(left[key]), float(right[key]))
        for key in sorted(left)
    }


def build_conservative_champion(
    v312_report: Mapping[str, Any],
    v32_report: Mapping[str, Any],
) -> ConservativeChampionEvidence:
    _validate_report_sha(v312_report, EXPECTED_V312_SHA256, "v3.1.2")
    _validate_report_sha(v32_report, EXPECTED_V32_SHA256, "v3.2")
    if v32_report.get("v312_dependency_report_sha256") != EXPECTED_V312_SHA256:
        raise TournamentV60Error("v3.2 is not cryptographically linked to v3.1.2")
    if not all(bool(value) for value in v312_report.get("gates", {}).values()):
        raise TournamentV60Error("v3.1.2 frozen integrity gates did not all pass")
    if not all(bool(value) for value in v32_report.get("gates", {}).values()):
        raise TournamentV60Error("v3.2 frozen replication gates did not all pass")

    standard = min(
        float(v312_report["standard"]["net_compounded_return"]),
        float(v32_report["standard"]["net_compounded_return"]),
    )
    stress = min(
        float(v312_report["stress"]["net_compounded_return"]),
        float(v32_report["stress"]["net_compounded_return"]),
    )
    # Conservative comparison uses the better cash outcome as the hurdle.
    cash = max(
        float(v312_report["standard"]["cash_benchmark_compounded_return"]),
        float(v32_report["standard"]["cash_benchmark_compounded_return"]),
    )
    standard_windows = _minimum_windows(
        v312_report["standard"]["window_returns"],
        v32_report["standard"]["window_returns"],
    )
    stress_windows = _minimum_windows(
        v312_report["stress"]["window_returns"],
        v32_report["stress"]["window_returns"],
    )
    years = len(standard_windows)
    annualized_standard = _annualize(standard, years)
    annualized_cash = _annualize(cash, years)
    return ConservativeChampionEvidence(
        strategy_id="v3.1.2-v3.2-yield-trend",
        years=years,
        standard_return=standard,
        stress_return=stress,
        cash_return=cash,
        annualized_standard_return=annualized_standard,
        annualized_cash_return=annualized_cash,
        annualized_excess_over_cash=annualized_standard - annualized_cash,
        standard_windows=standard_windows,
        stress_windows=stress_windows,
        actions=min(
            int(v312_report["standard"]["crypto_action_days"]),
            int(v32_report["standard"]["crypto_action_days"]),
        ),
        maximum_drawdown=max(
            float(v312_report["standard"]["maximum_drawdown"]),
            float(v32_report["standard"]["maximum_drawdown"]),
        ),
        maximum_positive_window_share=max(
            float(v312_report["standard"]["maximum_positive_year_share"]),
            float(v32_report["standard"]["maximum_positive_year_share"]),
        ),
        source_report_sha256={
            "binance": EXPECTED_V312_SHA256,
            "coinbase": EXPECTED_V32_SHA256,
        },
    )


def evaluate_material_gates(
    evidence: ConservativeChampionEvidence,
) -> MaterialGates:
    return MaterialGates(
        annualized_return_at_least_5pct=(
            evidence.annualized_standard_return >= 0.05
        ),
        annualized_excess_over_cash_at_least_2pct=(
            evidence.annualized_excess_over_cash >= 0.02
        ),
        positive_stress_return=evidence.stress_return > 0.0,
        four_of_five_positive_standard_windows=(
            sum(value > 0.0 for value in evidence.standard_windows.values()) >= 4
        ),
        three_of_five_positive_stress_windows=(
            sum(value > 0.0 for value in evidence.stress_windows.values()) >= 3
        ),
        at_least_30_actions=evidence.actions >= 30,
        drawdown_at_most_5pct=evidence.maximum_drawdown <= 0.05,
        positive_delayed_execution=(
            evidence.delayed_execution_return is not None
            and evidence.delayed_execution_return > 0.0
        ),
        trade_concentration_at_most_20pct=(
            evidence.maximum_positive_trade_share is not None
            and evidence.maximum_positive_trade_share <= 0.20
        ),
        window_concentration_at_most_50pct=(
            evidence.maximum_positive_window_share <= 0.50
        ),
        independent_source_linked=True,
        frozen_dependencies_exact=True,
    )


def build_report(
    v312_report: Mapping[str, Any],
    v32_report: Mapping[str, Any],
) -> dict[str, Any]:
    champion = build_conservative_champion(v312_report, v32_report)
    gates = evaluate_material_gates(champion)
    missing = [
        name
        for name, value in asdict(gates).items()
        if not value
    ]
    status = "STATISTICAL_GATES_PENDING" if gates.passed else "MATERIAL_GATES_FAILED"
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_continuous_paper": False,
        "champion": asdict(champion),
        "material_gates": asdict(gates),
        "failed_or_missing_material_gates": missing,
        "statistical_gates": {
            "moving_block_bootstrap": "PENDING_ALIGNED_DAILY_SERIES",
            "deflated_sharpe": "PENDING_COMPLETE_TRIAL_REGISTRY",
            "probability_of_backtest_overfitting": "PENDING_ALIGNED_TOURNAMENT_ARMS",
        },
        "other_frozen_arms": {
            "yielding_cash": "DERIVED_HURDLE_ONLY",
            "passive_btc_eth": "PENDING_COMMON_ACCOUNTING_ADAPTER",
            "v4.4_learned_control": "PENDING_FROZEN_BUNDLE_REPRODUCTION",
            "v4.4_plus_v5.2_overlay": "PENDING_COMPLETE_EVIDENCE_CHAIN_ADAPTER",
        },
        "status": status,
        "historical_breakthrough": False,
        "forward_breakthrough": False,
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run v6 conservative champion screen")
    parser.add_argument("--v312-json", type=Path, required=True)
    parser.add_argument("--v32-json", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_report(
        json.loads(args.v312_json.read_text(encoding="utf-8")),
        json.loads(args.v32_json.read_text(encoding="utf-8")),
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": report["status"],
        "annualized_return": report["champion"]["annualized_standard_return"],
        "annualized_excess_over_cash": report["champion"]["annualized_excess_over_cash"],
        "failed_or_missing_material_gates": report["failed_or_missing_material_gates"],
        "report_sha256": report["report_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
