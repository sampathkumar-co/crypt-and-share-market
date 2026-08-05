from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Sequence


@dataclass(frozen=True)
class WalkForwardFold:
    train_start: int
    train_end: int
    validation_start: int
    validation_end: int
    test_start: int
    test_end: int

    def validate(self) -> None:
        values = (
            self.train_start,
            self.train_end,
            self.validation_start,
            self.validation_end,
            self.test_start,
            self.test_end,
        )
        if any(v < 0 for v in values):
            raise ValueError("fold boundaries must be non-negative")
        if not (
            self.train_start < self.train_end
            <= self.validation_start < self.validation_end
            <= self.test_start < self.test_end
        ):
            raise ValueError("fold chronology is invalid")


def purged_walk_forward_splits(
    n_samples: int,
    *,
    train_size: int,
    validation_size: int,
    test_size: int,
    purge: int,
    embargo: int,
    step: int | None = None,
) -> Iterator[WalkForwardFold]:
    """Yield chronological folds with label purge and post-test embargo.

    End indices are exclusive. Purge separates train/validation and
    validation/test. Embargo advances the next fold beyond the prior test.
    """
    params = (n_samples, train_size, validation_size, test_size)
    if any(v <= 0 for v in params):
        raise ValueError("sample and window sizes must be positive")
    if purge < 0 or embargo < 0:
        raise ValueError("purge and embargo must be non-negative")
    if step is None:
        step = test_size + embargo
    if step <= 0:
        raise ValueError("step must be positive")

    origin = 0
    while True:
        train_start = origin
        train_end = train_start + train_size
        validation_start = train_end + purge
        validation_end = validation_start + validation_size
        test_start = validation_end + purge
        test_end = test_start + test_size
        if test_end > n_samples:
            return
        fold = WalkForwardFold(
            train_start=train_start,
            train_end=train_end,
            validation_start=validation_start,
            validation_end=validation_end,
            test_start=test_start,
            test_end=test_end,
        )
        fold.validate()
        yield fold
        origin += step


def assert_no_overlap(folds: Sequence[WalkForwardFold], *, embargo: int) -> None:
    """Ensure test intervals are chronological and embargo-separated."""
    if embargo < 0:
        raise ValueError("embargo must be non-negative")
    for fold in folds:
        fold.validate()
    for previous, current in zip(folds, folds[1:]):
        if current.test_start < previous.test_end + embargo:
            raise ValueError("test folds violate embargo")
