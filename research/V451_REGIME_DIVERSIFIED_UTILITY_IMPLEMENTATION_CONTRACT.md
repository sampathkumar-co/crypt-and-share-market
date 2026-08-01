# v4.5 Regime-Diversified Utility Implementation Contract

## Scope

The implementation is limited to a retrospective, paper-only decision-layer experiment over the frozen v4.3 model family and verified v4.4 cash accounting.

## Frozen dependencies

The implementation must import and reuse:

- v4.3 source loading, dataset construction, recency ensemble, specialist predictions, sealed windows, costs, and bundle serialization;
- v4.4 `DGS3MO` cash download, parsing, prior-day availability, and daily compounding; and
- the v4.4 frozen-baseline validation approach.

It must not edit the v4.3 or v4.4 model, simulator, reports, protocols, or acceptance gates.

## Required functions

The v4.5 module must expose testable functions for:

- normalized regime entropy;
- probability-weighted specialist metrics;
- cross-sectional downside exclusion;
- date-level decisions;
- costed yield-bearing simulation;
- blocked calibration scoring;
- sealed-window evaluation;
- frozen-baseline validation and campaign execution.

## Determinism

- Asset ties resolve alphabetically.
- Regime ties resolve by numeric regime ID.
- Configuration ties follow the protocol’s conservative deterministic order.
- No random model fitting occurs in v4.5; the provided frozen v4.3 bundle is reused.
- The report records the baseline report SHA, baseline bundle SHA, source inventory, protocol SHA, contract SHA, implementation SHA, runtime versions, and selected configuration.

## Data boundary

- Model fitting represented by the supplied bundle ends 2025-06-30.
- Configuration selection uses only July, August, and September 2025 blocked calibration periods.
- The five sealed windows are evaluated exactly once after configuration selection.
- Sealed-window output is never fed back into calibration.

## Failure behavior

The campaign must fail closed when:

- the baseline report hash is invalid;
- the baseline bundle does not match the baseline report;
- source inventory or dataset metadata changes;
- cash history is missing or starts after the dataset;
- no specialist mixture is available;
- calibration has no candidate configuration; or
- any same-day cash observation would be required.

## Safety assertions

The report must contain:

- `paper_only: true`
- `authorizes_trading: false`
- `authorizes_shadow_paper: true`
- `retrospective: true`
- `untouched_historical_dates: false`

The source must not contain exchange private keys, order placement, or live execution code.

## First-campaign acceptance

The first campaign is successful only when the existing historical gates pass, excluding independent replication, current-market smoke, and untouched historical dates. A historical pass is still not trading authorization.
