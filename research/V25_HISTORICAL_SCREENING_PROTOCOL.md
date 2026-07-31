# v2.5 Historical Screening Protocol

## Purpose

Track B is an exploratory historical replay of the already-frozen v2.5 decision router. It is implemented separately from the live forward campaign so historical outcomes cannot alter Track A evidence, thresholds, fingerprints, gates, holdouts or authorization flags.

Historical screening can reject an idea early or identify implementation defects. It cannot prove forward profitability, unlock Track A, authorize shadow paper, or justify live trading.

## Immutable separation

- Reuse `src/tradebot/research/forward_alpha_v25.py` without modifying its signal thresholds.
- Read only an explicitly supplied historical folder of normalized v2.0-compatible snapshots.
- Never read from or write to `forward-data/v2` inside the screening evaluator.
- Never modify v2.3, v2.4 or v2.5 forward decisions, manifests, readiness or holdout files.
- Every output sets `paper_only=true`, `authorizes_trading=false`, `authorizes_shadow_paper=false`, `forward_proof=false` and `eligible_for_promotion=false`.

## Required historical evidence

- Canonicalize duplicate hours exactly as v2.5: earliest valid capture by `captured_at_utc`, then `snapshot_id`.
- Require continuous hourly data. A missing hour breaks the replay block; no price, decision or return is fabricated.
- Require at least 178 continuous snapshots: 169 for the first decision plus the next-hour entry and eight-hour exit through `t+9`.
- Every selected asset must have a valid spot mid at entry and exit.
- Conflicting duplicate hours, malformed snapshots or missing prices fail closed.

## Decision replay

- Evaluate every eligible decision hour with exactly the trailing 169 completed snapshots.
- Use the frozen v2.5 combined router and its three families:
  1. residual momentum with microstructure confirmation;
  2. funding/basis state transition;
  3. sweep-and-replenishment continuation.
- Entry is the next completed hourly snapshot after decision hour `t`.
- Repeated signals for the same asset and family within four hours are one event; only the earliest may open a cohort.
- At most two assets, maximum 15% target per asset and 30% total target remain inherited from v2.5.

## Portfolio screening

For each holding horizon `H` in 2, 4 and 8 hours:

- divide each target weight by `H` before opening a cohort;
- use one shared non-levered paper wallet starting at 100,000 units;
- buy only with available cash;
- mark open cohorts at each hourly spot mid;
- exit completely at entry hour plus `H`;
- calculate standard friction at 10 bps each way and stress friction at 20 bps each way;
- liquidate no cohort early and fabricate no missing exit.

Report combined results, each family in isolation and leave-one-family-out results. Report net return, maximum drawdown, event count, active days, win rate and realized contribution by asset and family. Cash, 30%-BTC and 30%-equal-weight universe are descriptive benchmarks only.

## Interpretation boundary

The output status is one of:

- `INSUFFICIENT_HISTORICAL_DATA`;
- `HISTORICAL_SCREENING_COMPLETE`;
- `HISTORICAL_SCREENING_REJECTED` when the primary four-hour standard-cost return is non-positive or no qualifying event exists.

A positive result remains exploratory. It must not tune the frozen v2.5 router or replace the preregistered Track A forward test. Any later strategy revision requires a new version and a newly frozen protocol before seeing its forward outcomes.
