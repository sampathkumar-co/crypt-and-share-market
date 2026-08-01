# v4.6 Walk-Forward Selective Veto Implementation Contract

## Scope

Implement a paper-only selective risk overlay over v4.3 hard routing and v4.4 yield-bearing cash accounting.

## Required invariants

- The baseline decision path is exactly v4.3 hard routing.
- Veto output is always a subset of baseline-selected assets.
- No veto decision may add an asset, increase `top_n`, increase per-asset target, or act outside baseline rebalance/panic timing.
- The all-disabled veto must reproduce baseline decisions and accounting exactly.
- Prior-day-known cash rates and transaction costs remain unchanged.

## Walk-forward base models

Each protocol fold must independently:

1. fit recency regime classifiers and specialist regressors using only rows through its training end;
2. calibrate hard-routing thresholds only inside its base-calibration quarter; and
3. freeze the base bundle before its veto-validation quarter.

Dynamic training must preserve the v4.3 estimator families, model grid, utility formula, recency-window lengths, deterministic seeds, and calibration tie-break.

## Veto selection

- Per-fold dispersion thresholds are derived only from that fold's base-calibration period.
- The shared veto grid is evaluated only on the six veto-validation quarters.
- Eligibility constraints are enforced before lexicographic ranking.
- The all-disabled baseline is always eligible.
- The selected configuration and every rejected eligibility reason are recorded.

## Required test surfaces

Expose deterministic functions for:

- dynamic recency masks;
- dynamic base-bundle training;
- veto metric computation;
- subset-only decision transformation;
- yield-bearing veto simulation;
- fold eligibility and selection;
- final sealed aggregation.

Tests must prove:

- calibration and validation dates do not overlap;
- veto selections are subsets of baseline selections;
- disabled veto reproduces baseline decisions;
- no same-day cash rate is used;
- an ineligible higher-return configuration cannot outrank an eligible one;
- paper-only/no-trading flags are present.

## Failure behavior

Fail closed on invalid baseline report/bundle, source or dataset mismatch, insufficient fold data, missing specialists, unavailable dispersion calibration, missing cash history, or non-deterministic selection.

## Output fingerprints

Record protocol, contract, implementation, source inventory, baseline report, baseline bundle, runtime, walk-forward bundle summaries, selected veto, and report SHA-256.
