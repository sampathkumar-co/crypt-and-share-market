from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from dataclasses import asdict, dataclass
from math import comb, log, sqrt
from pathlib import Path
from typing import Any, Mapping

import numpy as np
from scipy.stats import kurtosis, norm, skew

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import champion_robustness_v601 as robustness_base
from tradebot.research import champion_robustness_v6011 as robustness_warmup
from tradebot.research import champion_statistical_gates_v602 as v602
from tradebot.research import common_accounting_tournament_v60 as tournament
from tradebot.research import historical_yield_trend_integrity_v312 as v312
from tradebot.research import historical_yield_trend_v31 as v31


SCHEMA_VERSION = "6.0.3-chronology-correct-statistical-audit"
PROTOCOL_PATH = Path("research/V603_CHRONOLOGY_CORRECT_STATISTICAL_AUDIT_PROTOCOL.md")
REGISTRY_PATH = Path("research/V603_DIRECT_LINEAGE_TRIAL_FLOOR.json")
PARTITIONS = 8
DSR_THRESHOLD = 0.95
PBO_THRESHOLD = 0.20


class ChampionChronologyV603Error(RuntimeError):
    """Raised when chronology-correct evidence cannot be reproduced."""


@dataclass(frozen=True)
class DeflatedSharpeAudit:
    observations: int
    observed_source: str
    observed_annualized_sharpe: float
    sample_skewness: float
    sample_excess_kurtosis: float
    direct_lineage_trial_floor: int
    corrected_grid_sharpe_std: float
    expected_maximum_sharpe: float
    probability: float
    passed: bool


@dataclass(frozen=True)
class PBOAudit:
    candidates: int
    observations: int
    partitions: int
    evaluated_splits: int
    probability_of_backtest_overfitting: float
    passed: bool


def annualized_sharpe(values: np.ndarray) -> float:
    if values.ndim != 1 or len(values) < 2:
        return 0.0
    volatility = float(np.std(values, ddof=0))
    if volatility <= 1e-15:
        return 0.0
    return float(np.mean(values) / volatility * sqrt(365.0))


def _relative_series_for_model(
    model: v31.ModelSpec,
    bars: Mapping[str, Mapping[Any, Any]],
    features: Mapping[Any, Mapping[str, v31.Features]],
    cash_returns: Mapping[Any, float],
) -> np.ndarray:
    relative: list[float] = []
    for period in v31.DISCOVERY_PERIODS:
        simulation = robustness_base.simulate_diagnostic(
            model,
            bars,
            features,
            cash_returns,
            period.start,
            period.end,
            v31.STRESS_COST,
            signal_lag_days=1,
        )
        cash_daily = v602._cash_daily_returns(
            cash_returns, period.start, period.end
        )
        if len(simulation.daily_returns) == len(cash_daily) + 1:
            cash_daily.append(0.0)
        if len(simulation.daily_returns) != len(cash_daily):
            raise ChampionChronologyV603Error(
                f"daily alignment failed for {model.model_id} in {period.name}"
            )
        relative.extend(
            (1.0 + float(strategy_return)) / (1.0 + float(cash_return)) - 1.0
            for strategy_return, cash_return in zip(
                simulation.daily_returns, cash_daily, strict=True
            )
        )
    values = np.asarray(relative, dtype=float)
    if not np.all(np.isfinite(values)):
        raise ChampionChronologyV603Error(
            f"non-finite corrected discovery returns for {model.model_id}"
        )
    return values


def reproduce_corrected_grid() -> tuple[
    dict[str, np.ndarray],
    dict[str, Any],
]:
    bars, features, cash_returns = robustness_base._load_binance()
    original_chosen, original_table = v31.select_model(
        bars, features, cash_returns
    )
    if original_chosen.model_id != v312.FROZEN_MODEL.model_id:
        raise ChampionChronologyV603Error(
            "original v3.1 selection no longer reproduces the frozen model"
        )
    if len(original_table) != 64 or len(v31.MODEL_GRID) != 64:
        raise ChampionChronologyV603Error("v3.1 grid is not exactly 64 models")

    series = {
        model.model_id: _relative_series_for_model(
            model, bars, features, cash_returns
        )
        for model in v31.MODEL_GRID
    }
    lengths = {len(values) for values in series.values()}
    if len(lengths) != 1:
        raise ChampionChronologyV603Error(
            "corrected v3.1 discovery series are not aligned"
        )
    sharpes = {
        model_id: annualized_sharpe(values)
        for model_id, values in series.items()
    }
    ordered = sorted(sharpes, key=lambda key: (sharpes[key], key), reverse=True)
    frozen_rank = ordered.index(v312.FROZEN_MODEL.model_id) + 1
    metadata = {
        "original_selected_model_id": original_chosen.model_id,
        "frozen_model_id": v312.FROZEN_MODEL.model_id,
        "original_selection_reproduced": True,
        "original_selection_table_sha256": hashlib.sha256(
            canonical_json(original_table).encode("utf-8")
        ).hexdigest(),
        "corrected_model_count": len(series),
        "corrected_observations_per_model": lengths.pop(),
        "corrected_grid_series_sha256": hashlib.sha256(
            b"".join(
                model_id.encode("utf-8")
                + series[model_id].astype("<f8", copy=False).tobytes()
                for model_id in sorted(series)
            )
        ).hexdigest(),
        "corrected_grid_sharpe_std": float(
            np.std(list(sharpes.values()), ddof=0)
        ),
        "frozen_model_corrected_discovery_sharpe": sharpes[
            v312.FROZEN_MODEL.model_id
        ],
        "frozen_model_corrected_discovery_sharpe_rank": frozen_rank,
        "corrected_grid_sharpes": dict(sorted(sharpes.items())),
    }
    return series, metadata


def probability_of_backtest_overfitting(
    strategy_returns: Mapping[str, np.ndarray],
    *,
    partitions: int = PARTITIONS,
) -> PBOAudit:
    names = tuple(sorted(strategy_returns))
    if len(names) < 2:
        raise ChampionChronologyV603Error("PBO needs at least two candidates")
    lengths = {len(strategy_returns[name]) for name in names}
    if len(lengths) != 1:
        raise ChampionChronologyV603Error("PBO candidate series do not align")
    observations = lengths.pop()
    if partitions < 4 or partitions % 2 or observations < partitions:
        raise ChampionChronologyV603Error("invalid PBO partition count")

    base, remainder = divmod(observations, partitions)
    blocks: list[np.ndarray] = []
    start = 0
    for index in range(partitions):
        stop = start + base + (1 if index < remainder else 0)
        blocks.append(np.arange(start, stop, dtype=int))
        start = stop

    half = partitions // 2
    negative = 0
    evaluated = 0
    for chosen_blocks in itertools.combinations(range(1, partitions), half - 1):
        in_blocks = (0, *chosen_blocks)
        out_blocks = tuple(
            index for index in range(partitions) if index not in in_blocks
        )
        in_indices = np.concatenate([blocks[index] for index in in_blocks])
        out_indices = np.concatenate([blocks[index] for index in out_blocks])
        in_scores = {
            name: annualized_sharpe(strategy_returns[name][in_indices])
            for name in names
        }
        winner = max(names, key=lambda name: (in_scores[name], name))
        out_scores = {
            name: annualized_sharpe(strategy_returns[name][out_indices])
            for name in names
        }
        ordered = sorted(names, key=lambda name: (out_scores[name], name))
        rank = ordered.index(winner) + 1
        percentile = (rank - 0.5) / len(names)
        logit_rank = log(percentile / (1.0 - percentile))
        negative += int(logit_rank < 0.0)
        evaluated += 1

    expected = comb(partitions - 1, half - 1)
    if evaluated != expected:
        raise ChampionChronologyV603Error(
            f"unexpected PBO split count {evaluated} != {expected}"
        )
    probability = negative / evaluated
    return PBOAudit(
        candidates=len(names),
        observations=observations,
        partitions=partitions,
        evaluated_splits=evaluated,
        probability_of_backtest_overfitting=probability,
        passed=probability <= PBO_THRESHOLD,
    )


def deflated_sharpe_audit(
    binance: np.ndarray,
    coinbase: np.ndarray,
    *,
    trial_count: int,
    sharpe_trial_std: float,
) -> DeflatedSharpeAudit:
    source_values = {"binance": binance, "coinbase": coinbase}
    source_sharpes = {
        name: annualized_sharpe(values)
        for name, values in source_values.items()
    }
    observed_source = min(source_sharpes, key=source_sharpes.get)
    values = source_values[observed_source]
    observed = source_sharpes[observed_source]
    sample_skew = float(skew(values, bias=False))
    sample_kurt = float(kurtosis(values, fisher=True, bias=False))
    benchmark = v602.expected_maximum_sharpe(
        trial_count, sharpe_std=sharpe_trial_std
    )
    variance_term = (
        1.0
        - sample_skew * observed
        + ((sample_kurt + 2.0) / 4.0) * observed**2
    )
    probability = 0.0
    if variance_term > 0.0:
        statistic = (
            (observed - benchmark)
            * sqrt(len(values) - 1.0)
            / sqrt(variance_term)
        )
        probability = float(norm.cdf(statistic))
    return DeflatedSharpeAudit(
        observations=len(values),
        observed_source=observed_source,
        observed_annualized_sharpe=observed,
        sample_skewness=sample_skew,
        sample_excess_kurtosis=sample_kurt,
        direct_lineage_trial_floor=trial_count,
        corrected_grid_sharpe_std=sharpe_trial_std,
        expected_maximum_sharpe=benchmark,
        probability=probability,
        passed=probability >= DSR_THRESHOLD,
    )


def _load_registry() -> tuple[dict[str, Any], str]:
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if payload.get("paper_only") is not True:
        raise ChampionChronologyV603Error("trial registry is not paper-only")
    if payload.get("authorizes_trading") is not False:
        raise ChampionChronologyV603Error("trial registry authorizes trading")
    count = sum(
        int(item["attempted_configurations"])
        for item in payload["experiments"]
    )
    if count != int(payload["trial_count_floor"]) or count != 224:
        raise ChampionChronologyV603Error("direct-lineage trial floor changed")
    return payload, hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()


def build_report(
    v312_report: Mapping[str, Any],
    v32_report: Mapping[str, Any],
    robustness_report: Mapping[str, Any],
    material_report: Mapping[str, Any],
) -> dict[str, Any]:
    tournament._validate_report_sha(
        v312_report, tournament.EXPECTED_V312_SHA256, "v3.1.2"
    )
    tournament._validate_report_sha(
        v32_report, tournament.EXPECTED_V32_SHA256, "v3.2"
    )
    robustness_digest = tournament._validate_self_hashed_report(
        robustness_report, "v6.0.1.1 robustness"
    )
    material_digest = tournament._validate_self_hashed_report(
        material_report, "v6 material screen"
    )
    if material_report.get("failed_or_missing_material_gates") != []:
        raise ChampionChronologyV603Error("material gates are not all passed")
    if not PROTOCOL_PATH.is_file() or not REGISTRY_PATH.is_file():
        raise ChampionChronologyV603Error("v6.0.3 protocol files are missing")
    protocol_digest = hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()
    registry, registry_digest = _load_registry()

    corrected_grid, grid_metadata = reproduce_corrected_grid()
    pbo = probability_of_backtest_overfitting(corrected_grid)

    binance = v602._source_relative_series(
        robustness_base._load_binance, v312_report
    )
    coinbase = v602._source_relative_series(
        robustness_warmup._load_coinbase_with_real_warmup, v32_report
    )
    if len(binance) != len(coinbase):
        raise ChampionChronologyV603Error("source verification series do not align")

    master_seed = int.from_bytes(
        hashlib.sha256(
            "|".join(
                [
                    tournament.EXPECTED_V312_SHA256,
                    tournament.EXPECTED_V32_SHA256,
                    robustness_digest,
                    material_digest,
                    protocol_digest,
                    registry_digest,
                ]
            ).encode("utf-8")
        ).digest()[:8],
        "big",
    )
    source_bootstrap: dict[str, list[dict[str, Any]]] = {}
    for source_index, (source, values) in enumerate(
        (("binance", binance), ("coinbase", coinbase))
    ):
        source_bootstrap[source] = [
            asdict(
                v602.moving_block_bootstrap(
                    values,
                    block_length=block_length,
                    resamples=v602.BOOTSTRAP_RESAMPLES,
                    seed=master_seed + source_index * 10_000 + block_length,
                )
            )
            for block_length in v602.BLOCK_LENGTHS
        ]
    bootstrap_passed = all(
        bool(item["passed"])
        for rows in source_bootstrap.values()
        for item in rows
    )

    dsr = deflated_sharpe_audit(
        binance,
        coinbase,
        trial_count=int(registry["trial_count_floor"]),
        sharpe_trial_std=float(grid_metadata["corrected_grid_sharpe_std"]),
    )
    gates = {
        "original_selection_reproduced": bool(
            grid_metadata["original_selection_reproduced"]
        ),
        "corrected_grid_complete": (
            grid_metadata["corrected_model_count"] == 64
            and grid_metadata["corrected_observations_per_model"] > 0
        ),
        "both_sources_bootstrap_positive": bootstrap_passed,
        "direct_lineage_dsr_floor": dsr.passed,
        "corrected_grid_pbo": pbo.passed,
        "material_gates_still_passed": True,
        "paper_only_boundary": True,
    }
    passed = all(gates.values())
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_continuous_paper": False,
        "preserves_v602_failure": True,
        "protocol_sha256": protocol_digest,
        "trial_registry_sha256": registry_digest,
        "source_reports": {
            "binance": tournament.EXPECTED_V312_SHA256,
            "coinbase": tournament.EXPECTED_V32_SHA256,
            "robustness": robustness_digest,
            "material_screen": material_digest,
        },
        "direct_lineage_trial_floor": registry,
        "grid_reproduction": grid_metadata,
        "source_verification": {
            "binance": {
                "observations": len(binance),
                "annualized_sharpe": annualized_sharpe(binance),
                "compounded_relative_return": v602._compounded(binance),
                "series_sha256": hashlib.sha256(
                    binance.astype("<f8", copy=False).tobytes()
                ).hexdigest(),
            },
            "coinbase": {
                "observations": len(coinbase),
                "annualized_sharpe": annualized_sharpe(coinbase),
                "compounded_relative_return": v602._compounded(coinbase),
                "series_sha256": hashlib.sha256(
                    coinbase.astype("<f8", copy=False).tobytes()
                ).hexdigest(),
            },
        },
        "source_specific_bootstrap": source_bootstrap,
        "deflated_sharpe": asdict(dsr),
        "corrected_grid_pbo": asdict(pbo),
        "gates": gates,
        "status": (
            "DIRECT_LINEAGE_STATISTICS_PASSED_COMPLETE_REGISTRY_PENDING"
            if passed
            else "CHRONOLOGY_CORRECT_STATISTICS_FAILED"
        ),
        "historical_breakthrough": False,
        "forward_breakthrough": False,
        "complete_pre_v31_registry": "PENDING",
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run v6.0.3 chronology-correct champion statistics"
    )
    parser.add_argument("--v312-json", type=Path, required=True)
    parser.add_argument("--v32-json", type=Path, required=True)
    parser.add_argument("--robustness-json", type=Path, required=True)
    parser.add_argument("--material-json", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_report(
        json.loads(args.v312_json.read_text(encoding="utf-8")),
        json.loads(args.v32_json.read_text(encoding="utf-8")),
        json.loads(args.robustness_json.read_text(encoding="utf-8")),
        json.loads(args.material_json.read_text(encoding="utf-8")),
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "gates": report["gates"],
                "source_verification": report["source_verification"],
                "source_specific_bootstrap": report[
                    "source_specific_bootstrap"
                ],
                "deflated_sharpe": report["deflated_sharpe"],
                "corrected_grid_pbo": report["corrected_grid_pbo"],
                "frozen_model_corrected_rank": report["grid_reproduction"][
                    "frozen_model_corrected_discovery_sharpe_rank"
                ],
                "report_sha256": report["report_sha256"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
