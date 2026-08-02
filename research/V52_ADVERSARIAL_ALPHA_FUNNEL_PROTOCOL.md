# v5.2 Adversarial Alpha Funnel Protocol

## Purpose

v5.2 replaces one-hypothesis-at-a-time iteration with a broad, deterministic
mechanism search followed by aggressive falsification.

The campaign may discover and rank hypotheses. It must not authorize trading,
claim a profitability breakthrough, or inspect a new untouched final period.

## Frozen baseline

- Paper-only and long-or-cash.
- Exact v4.4 crypto signal, risk, universe, cadence, costs and cash yield.
- Completed daily candles; fills remain at the next daily open.
- Candidate overlays may only reduce existing targets.
- No candidate may add an asset, increase exposure, or change portfolio ranking.

## Search boundary

The generator must emit exactly 100,000 raw hypotheses from a frozen grammar.
The grammar spans distinct mechanism families rather than only nearby numeric
settings:

1. Relative level and rolling-percentile states.
2. Trend slope, acceleration and reversal states.
3. Cross-asset breadth, dispersion and leadership states.
4. Futures positioning, basis, funding and flow states.
5. Volatility compression, expansion and correlation states.
6. Baseline-model confidence, disagreement and opportunity-set states.

Generation uses a fixed seed and records every sampled canonical specification.
The search cannot use sealed-return performance to generate or mutate ideas.

## Frozen grammar

- History windows: 20, 40, 60, 90, 120 and 180 completed days.
- Difference lags: 1, 3, 5 and 10 completed days.
- Relative thresholds: 0.10, 0.20, 0.30, 0.40, 0.60, 0.70, 0.80 and 0.90.
- Events: low state, high state, upward crossing and downward crossing.
- Event persistence: 1, 3, 7, 14 and 21 days.
- Exposure multipliers: 0.25, 0.50 and 0.75.
- Rolling statistics exclude the current observation from their reference set.
- Missing history disables the candidate rather than imputing future knowledge.

A hypothesis is the full tuple of source, aggregation, transform, history,
lag, event, threshold, persistence and multiplier.

## Funnel stages

### Stage A: structural rejection

Reject impossible, duplicate, constant, near-empty and near-always-active rules.
Behavioral deduplication uses packed intervention states on the exposed research
corpus. The simplest representative is retained for identical behavior.

### Stage B: cheap counterfactual screen

Estimate candidate value only against the frozen baseline's risky daily return.
Require useful coverage, positive compounded proxy excess, at least four positive
folds and no catastrophic fold. This proxy cannot certify a candidate.

### Stage C: adversarial attacks

Survivors are attacked with one- and two-day delays, nearby thresholds, nearby
history, deterministic active-day dropout, removal of the best fold, doubled
intervention cost and circular-shift placebo tests.

### Stage D: exact portfolio simulation

Only the strongest behaviorally distinct survivors receive exact simulations
with the frozen next-open portfolio engine. Exact standard and doubled-cost
results are required for every exposed walk-forward fold.

### Stage E: shortlist freeze

At most three distinct mechanism families may be shortlisted. A survivor must:

- improve at least four walk-forward folds;
- have positive compounded exact excess;
- have minimum exact fold excess of at least -0.25%;
- remain positive under doubled costs;
- survive delay and threshold-neighbor attacks;
- avoid dependence on one fold or a tiny number of interventions;
- never add an asset or increase a frozen target.

## Evidence separation

The v5.2 report is discovery evidence, not final profitability evidence.
It may use already exposed walk-forward dates through September 2025, but it
must label them exposed and retrospective. It must not evaluate the shortlist
on any newly designated untouched period.

A later version must freeze one shortlisted mechanism before independent
replication and genuinely forward paper observation.

## Failure behavior

If no candidate survives every frozen stage, the report must return an empty
shortlist and document the strongest rejected families. The disabled v4.4
baseline remains the only accepted strategy state.

No live trading is authorized under any outcome.
