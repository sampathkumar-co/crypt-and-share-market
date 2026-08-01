# v4.9 Dollar/Rates Probability-Shock Implementation Contract

## Safety

- `paper_only=True`
- `authorizes_trading=False`
- `authorizes_shadow_paper=True`
- long-or-cash only
- no broker, exchange order, wallet, key, or execution integration
- unchanged v4.4 crypto model, costs, cadence, universe, and cash-yield accounting
- active target multiplier fixed at `0.50`

## Source and availability

Reuse v4.7 source loading and manifests for `DTWEXBGS` and `DGS10`, including raw hashes, observation counts, requested fixed range, and strict prior-day-known lookup.

For decision date `D`, the newest usable macro observation and every lookback endpoint must be dated no later than `D - 1 day`.

Probability lookbacks may use only previously computed crypto-date probabilities. When an exact calendar lookback is absent, use the newest earlier crypto date. The current probability may never enter its own prior maximum window.

No future interpolation, same-day future observation, revised selection based on sealed outcomes, or asset-specific macro feature is permitted.

## Feature and model contract

Use exactly the v4.7 `dollar_rates` columns. Fit one independent regularized logistic model per fold on unique date-level samples.

Training preprocessing and classifier fitting must use only dates through the fold training end. The model and threshold must be frozen before validation.

## Shock contract

Implement exactly:

- `drop_5`
- `drop_20`
- `drawdown_20`

Every score must be non-negative. Improving or unchanged probability must produce zero shock.

The implementation must include causal unit tests proving:

- exact-date lookbacks use only past dates;
- missing lookbacks use the newest earlier date;
- `drawdown_20` excludes the current date from its prior maximum;
- changing a future probability cannot change an earlier shock score.

## Attenuation contract

At scheduled non-panic rebalances:

- baseline selected assets remain unchanged;
- when shock is at least the frozen threshold, each baseline target is multiplied by `0.50`;
- otherwise each baseline target remains unchanged;
- panic always targets cash;
- no target may exceed the corresponding v4.4 target;
- selected cardinality may not increase;
- no asset absent from the baseline target may receive exposure.

The simulation must report:

- attenuated-decision count;
- assets affected by attenuation;
- minimum and maximum applied multiplier;
- maximum selected cardinality;
- maximum target and gross exposure;
- proof that no asset was added and no target was increased.

## Calibration contract

For each active shock family and fold, evaluate only the fixed threshold grid from the protocol.

Divide the base-calibration quarter into calendar-month blocks. A threshold result must include every monthly baseline, attenuated result, and excess. Select the threshold using the exact protocol ordering.

The disabled baseline is not part of the active threshold grid.

## Walk-forward selection contract

Reuse all six v4.6 fold specifications and independently fitted base bundles.

For every shock family, preserve per-fold:

- training/calibration/validation boundaries;
- classifier sample count and label share;
- chosen threshold;
- monthly calibration results;
- validation baseline and attenuated summaries;
- validation excess.

Evaluate eligibility exactly as specified in the protocol. The disabled baseline must always be available and reproduce the corresponding v4.4 fold summaries with added zero-valued shock/attenuation audit fields only.

## Final evaluation contract

After family selection:

- fit the final macro classifier only through 2025-06-30;
- choose the final shock threshold only on the three monthly blocks from July through September 2025;
- evaluate the frozen five sealed windows once under standard and stress costs;
- apply prior-day-known DGS3MO yield to residual cash.

## Evidence contract

The JSON report must include:

- protocol and contract hashes;
- v4.8, v4.7, and v4.4 provenance hashes;
- source manifests;
- fixed feature names, shock-family definitions, multiplier, and threshold grid;
- all six fold specifications;
- all candidate family fold results and eligibility reasons;
- selected family and final threshold calibration;
- standard and stress sealed windows;
- return, drawdown, turnover, action, concentration, exposure, cash-contribution, shock, and attenuation metrics;
- comparison with v4.4;
- runtime versions;
- canonical report SHA-256.

The dedicated workflow must compile dependencies, run focused tests, reproduce the final frozen v4.3 baseline, execute v4.9, validate the evidence/safety boundary, and upload the v4.3 report, bundle, and v4.9 report.

No live trading is authorized.