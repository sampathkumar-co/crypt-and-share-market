# v6.0.3 Chronology-Correct Champion Statistical Audit

Status: frozen after v6.0.2 failed and before v6.0.3 outcomes are calculated.

## Preservation of the negative result

The v6.0.2 project-wide post-hoc stress remains authoritative and failed. Nothing in this audit replaces, relaxes or deletes that result.

## Question

Did the unchanged v3.1.2/v3.2 champion survive the selection process that could actually have produced it before v3.3 was frozen?

The later v5.2 100,000-hypothesis search cannot causally select an already frozen v3.1/v3.2 model. It remains relevant to a present-day project-wide champion search, but not to candidate-time selection inference.

## Frozen direct-lineage trial floor

Use `research/V603_DIRECT_LINEAGE_TRIAL_FLOOR.json`, totaling 224 known configurations across v2.8, v2.9, v3.0 and v3.1. This is a floor, not a complete pre-v3.1 project registry.

## Exact v3.1 tournament reproduction

- Download the same Binance BTC/ETH and prior-day H15 cash history.
- Reproduce the original 64-model v3.1 selection table and exact frozen chosen model.
- Independently run all 64 models through the corrected scheduled-execution simulator on the ten discovery quarters.
- Build aligned daily relative-return series versus yielding cash for every corrected model.
- Record the frozen model's rank under corrected discovery Sharpe, but do not reselect.

## Source-specific bootstrap

The v6.0.2 daily-minimum cross-source synthetic lower bound remains preserved. This audit separately asks whether each genuine exchange path is positive under dependence.

For Binance and Coinbase independently:

- use the exact lag-one standard-cost daily relative-return series;
- circular moving-block bootstrap with 20, 60 and 120-day blocks;
- 10,000 deterministic resamples per block;
- require the 2.5th percentile compounded relative return to be strictly positive for all six source/block tests.

## Candidate-time Deflated Sharpe

- observed Sharpe is the lower of the genuine Binance and Coinbase annualized daily relative-return Sharpes;
- `number_of_trials = 224` from the frozen direct-lineage floor;
- `sharpe_trial_std` is the population standard deviation of the 64 corrected v3.1 discovery-model Sharpes, not an arbitrary constant;
- skewness and excess kurtosis come from the worse-Sharpe genuine source series;
- require DSR probability >= 0.95.

Passing this floor does not close the incomplete-registry limitation.

## CSCV/PBO

Compute combinatorially symmetric cross-validation on the 64 corrected Binance discovery-model daily relative-return series using eight chronological partitions. For every unique half-partition split, select the in-sample Sharpe winner and rank it out-of-sample. Require PBO <= 0.20.

This PBO tests the internal v3.1 family tournament. It does not erase selection across earlier families.

## Gates

All must pass:

1. original v3.1 chosen model reproduced exactly;
2. all 64 corrected discovery series aligned and finite;
3. six source-specific bootstrap lower bounds positive;
4. direct-lineage-floor DSR probability >= 0.95;
5. corrected-grid PBO <= 0.20;
6. all v6 material gates remain passed;
7. all evidence remains paper-only and non-authorizing.

## Outcomes

- `CHRONOLOGY_CORRECT_STATISTICS_FAILED`
- `DIRECT_LINEAGE_STATISTICS_PASSED_COMPLETE_REGISTRY_PENDING`

Neither outcome is a forward breakthrough. v3.3 remains unchanged and is the only forward-promotion route.
