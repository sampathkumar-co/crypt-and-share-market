# v5.3 Untouched Nine-Month Replication Implementation Contract

## Immutable inputs

- v5.2 report SHA-256:
  `60f4d1b88dc0ef66d64a8ec4e192a56fdaf76a07182bde8e1567f17a61313ab2`
- v5.2 implementation commit:
  `f1bf4e7b9351353bc488aa41415a815ba79cad23`
- Final v4.3 bundle supplied to the CLI.
- Frozen v4.4 cash and execution simulator.
- Source inventory ending June 30, 2026.

The implementation must reproduce the exact v5.2 primary and secondary
canonical specifications before evaluating them.

## Candidate construction

Build one continuous date-level panel beginning at least 230 days before
October 1, 2025 and ending June 30, 2026. Candidate sources are derived only
from completed observations in `Dataset.X`.

Use v5.2 `transformed_series`, `rolling_percentile`, `persist_events` and
`activity_for_fold` without changing their semantics. Convert active dates to
the validated v4.8 attenuation adapter with threshold 0.5 and the frozen
candidate multiplier.

Activity generation must not restart at reporting-window boundaries.

## Evaluation adapter

Generate one probability map for every decision date from October 1, 2025
through June 30, 2026: active dates map to 0.0 and inactive dates map to 1.0.
Reuse `simulate_attenuation` with threshold 0.5.

Run exact candidate and baseline simulations under standard and stress costs for
the continuous interval, three calendar quarters and five existing sealed
windows. Reporting-window simulations retain the frozen v4.4 window semantics;
only candidate activity is generated continuously across boundaries.

## Excess-return definitions

For a single reporting window, excess return is candidate net return minus
baseline net return. Aggregate relative excess is candidate compounded growth
divided by baseline compounded growth minus one.

Maximum drawdown comparison is candidate drawdown minus the matching baseline
drawdown. Action and safety comparisons use the matching cost model and window.

The one-day-delay diagnostic shifts the complete activity array forward by one
decision date and inserts `False` at the beginning.

## Required report

The JSON evidence must contain:

- exact hashes of this protocol, contract, v5.2 report and bundle;
- source date range and source inventory reproduction;
- canonical primary and secondary specifications;
- continuous, quarterly and sealed-window baseline/candidate results;
- standard and stress excess metrics;
- every primary replication gate and its Boolean result;
- the one-day-delay diagnostic;
- unchanged v4.4 profitability gates;
- all safety invariants and final status.

The report must explicitly state:

- `paper_only: true`;
- `authorizes_trading: false`;
- `candidate_dates_untouched_before_v53: true`;
- `candidate_selection_performed_in_v53: false`;
- `candidate_parameters_changed_after_evaluation: false`.

If any frozen reproduction or safety assertion fails, execution must stop rather
than silently falling back or reporting a candidate result.
