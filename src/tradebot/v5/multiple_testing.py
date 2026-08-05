from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import comb, erf, exp, log, pi, sqrt
from statistics import mean, pstdev
from typing import Mapping, Sequence


_EULER_GAMMA = 0.5772156649015329


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def _normal_ppf(probability: float) -> float:
    if not 0.0 < probability < 1.0:
        raise ValueError("probability must be in (0, 1)")
    low, high = -10.0, 10.0
    for _ in range(100):
        midpoint = (low + high) / 2.0
        if _normal_cdf(midpoint) < probability:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


@dataclass(frozen=True)
class ExperimentAttempt:
    experiment_id: str
    family: str
    specification_sha256: str
    outcome_status: str
    consumed_intervals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.experiment_id or not self.family:
            raise ValueError("experiment_id and family are required")
        if len(self.specification_sha256) != 64:
            raise ValueError("specification_sha256 must be a SHA-256 hex digest")
        try:
            int(self.specification_sha256, 16)
        except ValueError as exc:
            raise ValueError("specification_sha256 must be hexadecimal") from exc


@dataclass(frozen=True)
class ExperimentRegistry:
    attempts: tuple[ExperimentAttempt, ...]

    def __post_init__(self) -> None:
        identifiers = [attempt.experiment_id for attempt in self.attempts]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("experiment IDs must be unique")
        consumed: set[str] = set()
        for attempt in self.attempts:
            overlap = consumed.intersection(attempt.consumed_intervals)
            if overlap:
                raise ValueError(f"consumed intervals reused: {sorted(overlap)}")
            consumed.update(attempt.consumed_intervals)

    @property
    def trial_count(self) -> int:
        return len(self.attempts)

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            [asdict(attempt) for attempt in self.attempts],
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(payload.encode()).hexdigest()

    def append(self, attempt: ExperimentAttempt) -> "ExperimentRegistry":
        return ExperimentRegistry(self.attempts + (attempt,))


def annualized_sharpe(returns: Sequence[float], *, periods_per_year: int = 365) -> float:
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    if len(returns) < 2:
        return 0.0
    volatility = pstdev(returns)
    if volatility == 0:
        return 0.0
    return mean(returns) / volatility * sqrt(periods_per_year)


def expected_maximum_sharpe(number_of_trials: int, *, sharpe_std: float = 1.0) -> float:
    """Expected maximum Sharpe under repeated independent null trials.

    This is the Bailey/Lopez de Prado extreme-value approximation. The caller
    must count every attempted configuration, including rejected experiments.
    """
    if number_of_trials <= 1:
        return 0.0
    if sharpe_std <= 0:
        raise ValueError("sharpe_std must be positive")
    first = _normal_ppf(1.0 - 1.0 / number_of_trials)
    second = _normal_ppf(1.0 - 1.0 / (number_of_trials * exp(1.0)))
    return sharpe_std * ((1.0 - _EULER_GAMMA) * first + _EULER_GAMMA * second)


def deflated_sharpe_probability(
    observed_sharpe: float,
    *,
    number_of_trials: int,
    observations: int,
    skewness: float = 0.0,
    excess_kurtosis: float = 0.0,
    sharpe_std: float = 1.0,
) -> float:
    """Probability that Sharpe exceeds the multiple-testing benchmark.

    Returns a value in [0, 1]. Non-normality enters through the finite-sample
    Sharpe variance correction. A result near one is stronger evidence.
    """
    if observations < 2:
        return 0.0
    benchmark = expected_maximum_sharpe(number_of_trials, sharpe_std=sharpe_std)
    variance_term = (
        1.0
        - skewness * observed_sharpe
        + ((excess_kurtosis + 2.0) / 4.0) * observed_sharpe**2
    )
    if variance_term <= 0:
        return 0.0
    statistic = (observed_sharpe - benchmark) * sqrt(observations - 1.0) / sqrt(variance_term)
    return min(1.0, max(0.0, _normal_cdf(statistic)))


def minimum_track_record_length(
    observed_sharpe: float,
    *,
    target_probability: float = 0.95,
    benchmark_sharpe: float = 0.0,
    skewness: float = 0.0,
    excess_kurtosis: float = 0.0,
) -> int:
    """Minimum observations required to establish Sharpe above a benchmark."""
    if observed_sharpe <= benchmark_sharpe:
        return 2**31 - 1
    z_score = _normal_ppf(target_probability)
    variance_term = (
        1.0
        - skewness * observed_sharpe
        + ((excess_kurtosis + 2.0) / 4.0) * observed_sharpe**2
    )
    if variance_term <= 0:
        return 2**31 - 1
    required = 1.0 + variance_term * (z_score / (observed_sharpe - benchmark_sharpe)) ** 2
    return max(2, int(required) + (0 if required.is_integer() else 1))


def _sharpe(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    volatility = pstdev(values)
    return 0.0 if volatility == 0 else mean(values) / volatility


def probability_of_backtest_overfitting(
    strategy_returns: Mapping[str, Sequence[float]],
    *,
    partitions: int = 8,
) -> float:
    """Estimate PBO using combinatorially symmetric cross-validation.

    Rows are aligned observations and columns are candidate strategies. For
    each half-partition split, the in-sample winner is ranked out-of-sample.
    PBO is the fraction whose logit rank is below zero (worse than median).
    """
    if len(strategy_returns) < 2:
        raise ValueError("at least two strategies are required")
    lengths = {len(values) for values in strategy_returns.values()}
    if len(lengths) != 1:
        raise ValueError("strategy return series must align")
    observations = lengths.pop()
    if partitions < 4 or partitions % 2:
        raise ValueError("partitions must be an even integer of at least four")
    if observations < partitions:
        raise ValueError("not enough observations for requested partitions")

    names = tuple(sorted(strategy_returns))
    base, remainder = divmod(observations, partitions)
    blocks: list[tuple[int, int]] = []
    start = 0
    for index in range(partitions):
        stop = start + base + (1 if index < remainder else 0)
        blocks.append((start, stop))
        start = stop

    half = partitions // 2
    negative_logits = 0
    evaluated = 0
    # Symmetric complements are equivalent; anchor block zero to count once.
    for mask in range(1 << partitions):
        if not mask & 1 or mask.bit_count() != half:
            continue
        in_indices: list[int] = []
        out_indices: list[int] = []
        for block_index, (left, right) in enumerate(blocks):
            target = in_indices if mask & (1 << block_index) else out_indices
            target.extend(range(left, right))

        in_scores = {
            name: _sharpe([strategy_returns[name][i] for i in in_indices])
            for name in names
        }
        winner = max(names, key=lambda name: (in_scores[name], name))
        out_scores = {
            name: _sharpe([strategy_returns[name][i] for i in out_indices])
            for name in names
        }
        ordered = sorted(names, key=lambda name: (out_scores[name], name))
        rank = ordered.index(winner) + 1
        percentile = (rank - 0.5) / len(names)
        logit = log(percentile / (1.0 - percentile))
        negative_logits += int(logit < 0.0)
        evaluated += 1

    if evaluated != comb(partitions - 1, half - 1):
        raise AssertionError("unexpected CSCV split count")
    return negative_logits / evaluated
