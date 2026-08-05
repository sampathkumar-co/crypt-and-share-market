from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from math import exp, sqrt
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
from scipy.stats import kurtosis, norm, skew

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import champion_robustness_v601 as robustness_base
from tradebot.research import champion_robustness_v6011 as robustness_warmup
from tradebot.research import common_accounting_tournament_v60 as tournament
from tradebot.research import historical_yield_trend_integrity_v312 as v312
from tradebot.research import historical_yield_trend_v31 as v31


SCHEMA_VERSION = "6.0.2-champion-statistical-gates"
PROTOCOL_PATH = Path("research/V602_CHAMPION_STATISTICAL_GATES_PROTOCOL.md")
BLOCK_LENGTHS = (20, 60, 120)
BOOTSTRAP_RESAMPLES = 10_000
TRIAL_COUNT_FLOOR = 100_000
SHARPE_TRIAL_STD = 1.0
_EULER_GAMMA = 0.5772156649015329


class ChampionStatisticalV602Error(RuntimeError):
    """Raised when the frozen statistical evidence cannot be reproduced."""


@dataclass(frozen=True)
class BootstrapResult:
    block_length: int
    resamples: int
    observed_compounded_relative_return: float
    lower_95_bound: float
    median: float
    upper_95_bound: float
    passed: bool


@dataclass(frozen=True)
class DeflatedSharpeFloor:
    observations: int
    periods_per_year: int
    annualized_sharpe: float
    sample_skewness: float
    sample_excess_kurtosis: float
    trial_count_floor: int
    expected_maximum_sharpe: float
    probability: float
    passed: bool


def _compounded(values: np.ndarray) -> float:
    if np.any(values <= -1.0):
        raise ChampionStatisticalV602Error("relative return is at or below -100%")
    return float(np.expm1(np.log1p(values).sum()))


def _cash_daily_returns(cash_returns, start, end) -> list[float]:
    values: list[float] = []
    day = start
    while day <= end:
        values.append(float(cash_returns[day]))
        day += timedelta(days=1)
    return values


def _source_relative_series(
    loader: Callable[[], tuple[Any, Any, Any]],
    authoritative: Mapping[str, Any],
) -> np.ndarray:
    bars, features, cash_returns = loader()
    relative: list[float] = []
    yearly_strategy: list[float] = []
    yearly_cash: list[float] = []
    for period in v31.VERIFICATION_PERIODS:
        simulation = robustness_base.simulate_diagnostic(
            v312.FROZEN_MODEL,
            bars,
            features,
            cash_returns,
            period.start,
            period.end,
            v31.STANDARD_COST,
            signal_lag_days=1,
        )
        cash_daily = _cash_daily_returns(
            cash_returns, period.start, period.end
        )
        if len(simulation.daily_returns) == len(cash_daily) + 1:
            cash_daily.append(0.0)
        if len(simulation.daily_returns) != len(cash_daily):
            raise ChampionStatisticalV602Error(
                f"daily strategy/cash length mismatch in {period.name}"
            )
        yearly_strategy.append(simulation.net_return)
        yearly_cash.append(simulation.cash_return)
        for strategy_return, cash_return in zip(
            simulation.daily_returns, cash_daily, strict=True
        ):
            relative.append(
                (1.0 + float(strategy_return)) / (1.0 + cash_return) - 1.0
            )

    observed_strategy = robustness_base._compounded(yearly_strategy)
    observed_cash = robustness_base._compounded(yearly_cash)
    expected_strategy = float(
        authoritative["standard"]["net_compounded_return"]
    )
    expected_cash = float(
        authoritative["standard"]["cash_benchmark_compounded_return"]
    )
    if abs(observed_strategy - expected_strategy) > 1e-12:
        raise ChampionStatisticalV602Error("source strategy reproduction changed")
    if abs(observed_cash - expected_cash) > 1e-12:
        raise ChampionStatisticalV602Error("source cash reproduction changed")
    values = np.asarray(relative, dtype=float)
    exact_relative = (1.0 + observed_strategy) / (1.0 + observed_cash) - 1.0
    if abs(_compounded(values) - exact_relative) > 1e-12:
        raise ChampionStatisticalV602Error(
            "daily relative series does not reconcile to compounded evidence"
        )
    return values


def conservative_relative_series(
    v312_report: Mapping[str, Any],
    v32_report: Mapping[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    binance = _source_relative_series(
        robustness_base._load_binance, v312_report
    )
    coinbase = _source_relative_series(
        robustness_warmup._load_coinbase_with_real_warmup, v32_report
    )
    if len(binance) != len(coinbase):
        raise ChampionStatisticalV602Error(
            "Binance and Coinbase daily series do not align"
        )
    conservative = np.minimum(binance, coinbase)
    metadata = {
        "observations": int(len(conservative)),
        "binance_series_sha256": hashlib.sha256(
            binance.astype("<f8", copy=False).tobytes()
        ).hexdigest(),
        "coinbase_series_sha256": hashlib.sha256(
            coinbase.astype("<f8", copy=False).tobytes()
        ).hexdigest(),
        "conservative_series_sha256": hashlib.sha256(
            conservative.astype("<f8", copy=False).tobytes()
        ).hexdigest(),
        "binance_compounded_relative_return": _compounded(binance),
        "coinbase_compounded_relative_return": _compounded(coinbase),
        "conservative_compounded_relative_return": _compounded(conservative),
        "construction": "daily_minimum_cross_source_relative_return_lower_bound",
    }
    return conservative, metadata


def moving_block_bootstrap(
    values: np.ndarray,
    *,
    block_length: int,
    resamples: int,
    seed: int,
) -> BootstrapResult:
    if values.ndim != 1 or len(values) < block_length:
        raise ChampionStatisticalV602Error("invalid bootstrap series or block")
    if resamples < 100:
        raise ChampionStatisticalV602Error("bootstrap resamples are too few")
    log_values = np.log1p(values)
    n = len(log_values)
    extended = np.concatenate([log_values, log_values[: block_length - 1]])
    full_blocks, remainder = divmod(n, block_length)
    full_sums = np.asarray(
        [extended[index : index + block_length].sum() for index in range(n)]
    )
    partial_sums = (
        np.asarray(
            [extended[index : index + remainder].sum() for index in range(n)]
        )
        if remainder
        else None
    )
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, size=(resamples, full_blocks))
    sampled_logs = full_sums[starts].sum(axis=1)
    if remainder and partial_sums is not None:
        tail_starts = rng.integers(0, n, size=resamples)
        sampled_logs += partial_sums[tail_starts]
    sampled = np.expm1(sampled_logs)
    lower, median, upper = np.quantile(sampled, [0.025, 0.5, 0.975])
    observed = _compounded(values)
    return BootstrapResult(
        block_length=block_length,
        resamples=resamples,
        observed_compounded_relative_return=observed,
        lower_95_bound=float(lower),
        median=float(median),
        upper_95_bound=float(upper),
        passed=bool(lower > 0.0),
    )


def expected_maximum_sharpe(
    number_of_trials: int, *, sharpe_std: float = 1.0
) -> float:
    if number_of_trials <= 1:
        return 0.0
    first = norm.ppf(1.0 - 1.0 / number_of_trials)
    second = norm.ppf(1.0 - 1.0 / (number_of_trials * exp(1.0)))
    return float(
        sharpe_std
        * ((1.0 - _EULER_GAMMA) * first + _EULER_GAMMA * second)
    )


def deflated_sharpe_floor(values: np.ndarray) -> DeflatedSharpeFloor:
    if len(values) < 2:
        raise ChampionStatisticalV602Error("not enough Sharpe observations")
    volatility = float(np.std(values, ddof=0))
    annualized = (
        0.0
        if volatility == 0.0
        else float(np.mean(values) / volatility * sqrt(365.0))
    )
    sample_skew = float(skew(values, bias=False))
    sample_kurt = float(kurtosis(values, fisher=True, bias=False))
    benchmark = expected_maximum_sharpe(
        TRIAL_COUNT_FLOOR, sharpe_std=SHARPE_TRIAL_STD
    )
    variance_term = (
        1.0
        - sample_skew * annualized
        + ((sample_kurt + 2.0) / 4.0) * annualized**2
    )
    probability = 0.0
    if variance_term > 0.0:
        statistic = (
            (annualized - benchmark)
            * sqrt(len(values) - 1.0)
            / sqrt(variance_term)
        )
        probability = float(norm.cdf(statistic))
    return DeflatedSharpeFloor(
        observations=int(len(values)),
        periods_per_year=365,
        annualized_sharpe=annualized,
        sample_skewness=sample_skew,
        sample_excess_kurtosis=sample_kurt,
        trial_count_floor=TRIAL_COUNT_FLOOR,
        expected_maximum_sharpe=benchmark,
        probability=probability,
        passed=probability >= 0.95,
    )


def _seed(*digests: str) -> int:
    material = "|".join(digests).encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def build_report(
    v312_report: Mapping[str, Any],
    v32_report: Mapping[str, Any],
    robustness_report: Mapping[str, Any],
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
    if robustness_report.get("status") != "CHAMPION_ROBUSTNESS_PASSED":
        raise ChampionStatisticalV602Error(
            "material robustness did not pass before statistics"
        )
    if not PROTOCOL_PATH.is_file():
        raise ChampionStatisticalV602Error("statistical protocol is missing")
    protocol_digest = hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()

    values, series_metadata = conservative_relative_series(
        v312_report, v32_report
    )
    master_seed = _seed(
        tournament.EXPECTED_V312_SHA256,
        tournament.EXPECTED_V32_SHA256,
        robustness_digest,
        protocol_digest,
    )
    bootstrap = [
        moving_block_bootstrap(
            values,
            block_length=block_length,
            resamples=BOOTSTRAP_RESAMPLES,
            seed=master_seed + block_length,
        )
        for block_length in BLOCK_LENGTHS
    ]
    dsr = deflated_sharpe_floor(values)
    bootstrap_passed = all(item.passed for item in bootstrap)
    status = (
        "PBO_AND_COMPLETE_REGISTRY_PENDING"
        if bootstrap_passed and dsr.passed
        else "STATISTICAL_GATES_FAILED"
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_continuous_paper": False,
        "protocol_sha256": protocol_digest,
        "source_reports": {
            "binance": tournament.EXPECTED_V312_SHA256,
            "coinbase": tournament.EXPECTED_V32_SHA256,
            "robustness": robustness_digest,
        },
        "series": series_metadata,
        "bootstrap": [asdict(item) for item in bootstrap],
        "bootstrap_gate_passed": bootstrap_passed,
        "deflated_sharpe_floor": asdict(dsr),
        "pbo": "PENDING_ALL_COMMON_ACCOUNTING_ARMS",
        "complete_trial_registry": "PENDING_APPEND_ONLY_PROJECT_REGISTRY",
        "status": status,
        "historical_breakthrough": False,
        "forward_breakthrough": False,
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run v6.0.2 champion statistical gates"
    )
    parser.add_argument("--v312-json", type=Path, required=True)
    parser.add_argument("--v32-json", type=Path, required=True)
    parser.add_argument("--robustness-json", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_report(
        json.loads(args.v312_json.read_text(encoding="utf-8")),
        json.loads(args.v32_json.read_text(encoding="utf-8")),
        json.loads(args.robustness_json.read_text(encoding="utf-8")),
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
                "series": report["series"],
                "bootstrap": report["bootstrap"],
                "deflated_sharpe_floor": report[
                    "deflated_sharpe_floor"
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
