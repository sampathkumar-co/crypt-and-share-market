# v5.1 Consensus Transition-Threshold Implementation Contract

## Safety

- `paper_only=True`
- `authorizes_trading=False`
- `authorizes_shadow_paper=True`
- long-or-cash only
- fixed family `fresh_14d`
- fixed multiplier `0.50`
- unchanged v4.4 model, costs, cadence, cash yield, universe, targets, and gates
- disabled v4.4 exact fallback

## Reproduction contract

Rebuild all six v5.0 walk-forward folds and independently calibrate the `fresh_14d` threshold on each fold's three monthly base-calibration blocks.

Preserve:

- fold boundaries;
- macro training count and label share;
- per-fold calibrated threshold;
- calibration monthly results;
- validation baseline and transition summaries;
- validation excess;
- original v5.0 eligibility result.

The reproduced family must be eligible under the unchanged v5.0 rules before consensus evaluation continues.

## Consensus calculation

Implement a pure deterministic function that:

1. accepts exactly six thresholds;
2. verifies every threshold belongs to the frozen v5.0 grid;
3. sorts them;
4. computes the ordinary median;
5. returns the median when it belongs to the grid;
6. otherwise returns the smallest grid threshold greater than the median.

The report must contain the chronological list, sorted list, raw median, rounded consensus threshold, grid, and proof that all source calibration periods end no later than 2025-06-30.

Unit tests must cover:

- exact-grid median;
- between-grid median rounded upward;
- invalid count;
- off-grid input rejection;
- deterministic ordering independence.

## Fixed-threshold fold audit

Using the already fitted fold base bundles and macro models, recompute each validation fold with `fresh_14d`, the single consensus threshold, and multiplier `0.50`.

Do not recalibrate the threshold per fold during this audit.

Preserve all fold baseline/transition metrics and apply the exact v5.0 family eligibility rules. If the fixed-threshold audit is ineligible, select disabled and skip active sealed evaluation.

## Final model and audit quarter

If eligible:

- fit the final macro model using dates through 2025-06-30 only;
- compute causal probabilities and fresh-transition states using the frozen consensus threshold;
- evaluate July, August, and September 2025 separately as audit-only blocks;
- do not modify any rule based on the audit quarter.

The final report must clearly distinguish `audit_only=True` from calibration or selection.

## Sealed evaluation

If eligible, evaluate the frozen rule on the same five exposed sealed windows once under standard and stress costs. If ineligible, reproduce v4.4 exactly.

The simulation must retain all v5.0 safety audit fields, including crossings, active-transition dates, attenuated decisions, affected assets, multiplier bounds, exposure, cardinality, actions, turnover, drawdown, cash, asset, and regime contribution.

## Evidence

The JSON report must include:

- protocol and contract hashes;
- v5.0 report/protocol provenance;
- v4.9, v4.8, v4.7, and v4.4 provenance;
- source manifests and runtime versions;
- original per-fold calibrated results;
- consensus derivation;
- fixed-threshold fold audit and eligibility reasons;
- final audit-only quarter;
- sealed standard/stress results;
- comparison with v4.4 and v5.0;
- original gates and canonical report SHA-256.

The dedicated workflow must compile dependencies, run all focused tests, reproduce v4.3/v4.4 and v5.0 fold evidence, execute v5.1, validate consensus derivation and safety invariants, and upload the evidence bundle.

No live trading is authorized.