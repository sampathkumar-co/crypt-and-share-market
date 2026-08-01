# v5.0 Fresh Macro-Transition Implementation Contract

## Safety and provenance

- `paper_only=True`
- `authorizes_trading=False`
- `authorizes_shadow_paper=True`
- long-or-cash only
- unchanged v4.4 crypto model, costs, cadence, universe, targets, cash-yield accounting, and gates
- fixed active multiplier `0.50`
- disabled baseline exact fallback

The report must preserve v4.9, v4.8, v4.7, and v4.4 provenance hashes.

## Causal transition construction

For each independently fitted fold macro model:

1. compute probabilities on sorted crypto dates using prior-day-known macro features;
2. compare each probability only with the immediately preceding available crypto date;
3. open an episode only on a causal crossing from `>= threshold` to `< threshold`;
4. keep the episode open while probability remains below threshold;
5. rearm only after probability is `>= threshold`;
6. mark a date active only when elapsed calendar time from the episode crossing is less than the family window;
7. never restart the window inside the same low-state episode.

The first date may not be treated as a crossing because no prior probability is available.

Unit tests must prove:

- downward crossing detection;
- no activation without a prior above-threshold date;
- no window restart during a persistent low state;
- recovery and later recrossing rearm the controller;
- exact 3-, 7-, and 14-calendar-day boundaries;
- changing a future probability cannot alter earlier activity.

## Simulation

Reuse the v4.8 tested portfolio simulator through a transition-state adapter.

At scheduled non-panic rebalances:

- active transition state maps to multiplier `0.50`;
- inactive state maps to multiplier `1.0`;
- baseline selected assets remain unchanged;
- panic targets cash;
- target exposure and cardinality may not exceed baseline;
- no asset absent from the baseline target may receive exposure.

Report transition family, threshold, crossing count, active date count, attenuated-decision count, affected assets, minimum applied multiplier, exposure, cardinality, action, turnover, drawdown, cash, asset, and regime metrics.

## Fold calibration

For each family and fold:

- independently train/calibrate the frozen v4.3 base bundle;
- fit the dollar/rates classifier only through the fold training end;
- evaluate exactly the fixed threshold grid on the base-calibration quarter;
- preserve all three monthly baseline and transition summaries;
- apply the exact protocol tie-break order;
- freeze the threshold before validation.

## Walk-forward family selection

Preserve six fold results for each of `fresh_3d`, `fresh_7d`, and `fresh_14d`.

Apply the exact frozen eligibility rules. Rank only eligible active families. Disabled is not part of the active ranking and wins only when no active family qualifies.

The disabled candidate must preserve exact fold baseline summaries with zero-valued transition audit fields.

## Final calibration and sealed evaluation

- final macro training ends 2025-06-30;
- final threshold calibration uses only July–September 2025;
- selected family and threshold are frozen before sealed evaluation;
- evaluate the same five windows once under standard and stress costs;
- residual cash receives prior-day-known DGS3MO yield.

## Evidence and CI

The JSON report must contain:

- protocol/contract/provenance hashes;
- source manifests and runtime versions;
- fixed feature names, transition families, window lengths, threshold grid, and multiplier;
- all fold specifications and candidate results;
- family eligibility reasons and deterministic selection key;
- final calibration blocks;
- sealed standard/stress results and original gates;
- exact comparison with v4.4;
- canonical report SHA-256.

The dedicated workflow must compile dependencies, run focused tests, reproduce the frozen v4.3/v4.4 baseline, execute v5.0, validate all evidence/safety/selection invariants, and upload the evidence bundle.

No live trading is authorized.