# v2.4 Pre-Implementation Evaluation Clarifications

This document is frozen before evaluator implementation and before any v2.3
return, P&L, drawdown, benchmark result, confidence interval or holdout byte is
read. It narrows ambiguities in `V24_FORWARD_ALPHA_EVALUATION_PROTOCOL.md`
without changing the frozen v2.3 candidate rules, costs, gates or safety flags.

## 1. Evaluator readiness is 1,448 hours

The v2.3 readiness report may announce sealed-outcome attachment readiness at
1,440 eligible decisions because that is its independently frozen inventory
boundary. That status does not authorize v2.4 performance calculation.

The v2.4 evaluator remains readiness-only until all of the following exist and
verify:

- 1,448 contiguous eligible v2.3 decisions;
- decisions 1–1,104 for discovery;
- decisions 1,105–1,112 as an unscored purge/embargo;
- decisions 1,113–1,448 as the untouched 336-hour holdout;
- every required next-hour entry snapshot; and
- every two-, four- and eight-hour exit snapshot through the final `t+9`.

A 1,440-hour v2.3 readiness status alone must not load or calculate any outcome.

## 2. Canonical hourly observation

For every UTC hour, v2.4 uses the same deterministic canonicalization frozen by
v2.1 and v2.3: the earliest valid persisted v2.0 capture in that hour, ordered
by `captured_at_utc` and then `snapshot_id`. Later duplicate captures are not
averaged, substituted or selected using outcome information.

## 3. No-look-ahead selected-assets passive benchmark

The original phrase “assets selected at least once in discovery” must not be
used to choose holdings at the discovery first fill, because that would use
future discovery selections.

The selected-assets passive benchmark is instead constructed online:

- it starts with 100% cash;
- when an asset is selected for the first time by a completed discovery
  decision, that asset becomes eligible from the same decision’s next-hour fill;
- eligible assets remain in the benchmark through the discovery final mark;
- at each new eligibility event, the benchmark rebalances its 40% exposure
  equally across assets eligible at that time and retains 60% cash;
- it pays the same entry/exit friction on actual benchmark turnover;
- no asset may enter before its first observed selection; and
- holdout selections can never affect the discovery benchmark.

This benchmark remains mandatory for the frozen discovery comparison.

## 4. Exact moving-block bootstrap

“UTC day-block bootstrap” means a deterministic circular moving-block bootstrap
of discovery daily returns:

- block length: exactly three consecutive UTC days;
- resamples: exactly 20,000;
- random seed: exactly `2304`;
- each replicate repeatedly draws a start-day index uniformly with replacement,
  takes that day and the following two days with circular wraparound, concatenates
  blocks, and truncates to the original discovery-day count;
- the statistic is the arithmetic mean daily return of the truncated replicate;
- the 95% interval uses the 2.5th and 97.5th percentiles with linear percentile
  interpolation; and
- the lower endpoint must be strictly greater than zero.

If fewer than six discovery day-end returns exist, the confidence gate fails
closed rather than changing the block length.

## 5. Exact pinned support module

The support SHA-256 in the parent protocol refers specifically to:

`src/tradebot/research/forward_paper_evaluation.py`

Required SHA-256:

`444de074af9476b8e16cf0f219aec5700b2de2fa284025f31d2ee9dceaa8478f`

No other v2.2 module is implicitly covered by that fingerprint.

## Safety boundary

These clarifications do not authorize shadow paper or live trading. All outputs
remain paper-only with `authorizes_trading=false` and
`authorizes_shadow_paper=false`. Discovery rejection remains terminal and must
not read, hash, parse or summarize holdout files.
