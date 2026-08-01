# v4.7 Macro-Liquidity State Implementation Contract

## Safety

- `paper_only=True`
- `authorizes_trading=False`
- `authorizes_shadow_paper=True`
- long-or-cash only
- no broker, exchange order, wallet, key, or execution integration
- unchanged v4.4 costs, cadence, exposure, crypto universe, and cash-yield accounting

## Source contract

The implementation must use only the fixed public FRED graph CSV endpoints declared in code for:

- `VIXCLS`
- `DTWEXBGS`
- `DGS10`
- `NASDAQCOM`

Requested history must start at `2022-01-01` and end at `2026-06-30`.

For every source, fail closed on:

- network/HTTP failure;
- empty payload;
- invalid UTF-8;
- missing date or series column;
- duplicate dated observation;
- non-finite or non-positive values where the series requires positive values;
- insufficient observations;
- history that does not cover the required feature lookback.

Record raw SHA-256, observation count, first date, last date, provider, series, units handling, and URL.

## Availability contract

For a crypto decision timestamp `D`, use the newest macro observation dated `<= D - 1 day`.

For a lookback of `N` calendar days, use the newest observation dated `<= D - 1 day - N days`.

No interpolation from future observations is permitted. Missing required prior history must fail closed.

## Feature contract

Macro features are deterministic, finite, date-level, and replicated unchanged across all assets for a date.

The macro matrix must preserve the dataset row order. Feature names and family column indexes must be explicit and stable.

No crypto-derived feature may enter the macro classifier. The forward market label may be derived only from the existing dataset labels and must be aggregated once per unique date.

## Walk-forward contract

Reuse the six v4.6 fold specifications and independently fitted fold bundles.

For each family and fold:

- fit one regularized logistic classifier on unique training dates;
- fit preprocessing only on the training dates;
- choose a threshold only from the fixed grid on the base-calibration quarter;
- freeze the model and threshold before validation;
- compare against the same fold's v4.4 baseline including DGS3MO cash yield.

The disabled family is an explicit baseline candidate.

## Action contract

The macro gate may only turn a baseline non-panic selected target into an empty target at a scheduled rebalance.

It must preserve:

- baseline panic decisions;
- baseline selected asset identity whenever a trade is allowed;
- baseline target weight;
- baseline rebalance age/cadence;
- baseline transaction-cost assumptions.

The report must prove that the gate never added an asset, never increased maximum target exposure, and never increased selected cardinality.

## Selection contract

An active family is eligible only when every criterion in the protocol is satisfied. Selection ordering is:

1. minimum validation-fold excess;
2. number of positive-excess folds;
3. compounded validation excess;
4. worst validation return;
5. lower maximum drawdown;
6. lower turnover;
7. fewer actions;
8. fewer gated decisions;
9. lexical family name.

The disabled baseline must win whenever no active family is eligible.

## Evidence contract

The JSON report must include:

- source manifests and hashes;
- macro feature names and family definitions;
- six fold training/calibration/validation boundaries;
- per-fold classifier and threshold metadata;
- per-fold baseline and gated results;
- family eligibility reasons;
- final calibration result;
- five-window standard and stress results;
- cash contribution;
- actions, turnover, drawdown, selected assets, gated-decision counts;
- unchanged safety flags;
- runtime versions;
- protocol and contract hashes;
- canonical report SHA-256.

The workflow must compile, run focused tests, reproduce the final frozen v4.3 baseline, execute v4.7, validate the safety/evidence boundary, and upload the v4.3 report, bundle, and v4.7 report.
