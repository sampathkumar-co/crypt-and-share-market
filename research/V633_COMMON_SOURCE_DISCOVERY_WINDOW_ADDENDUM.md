# v6.3.3 Common-Source Discovery Window Addendum

Status: frozen after the first v6.3 workflow failed before producing any v6.3 candidate report or market outcome.

## Defect discovered

The v6.3 protocol requires a rank-stability comparison between the dual-source candidate and the 16 frozen Binance v6.1 members. The first implementation attempted to construct the dual-source candidate over all original v3.1 discovery quarters beginning 2018-07-01. Coinbase source history used by the frozen replication does not cover those early quarters, so execution failed closed with a missing-date error before any v6.3 result was calculated.

## Frozen correction

The rank-stability comparison uses the maximum complete, quarter-aligned discovery interval available to both frozen sources before the 2021-2025 verification period:

- 2020-Q3: 2020-07-01 through 2020-09-30;
- 2020-Q4: 2020-10-01 through 2020-12-31.

The dual-source candidate and every one of the 16 comparison members are evaluated on exactly these same 184 chronological observations. The existing eight-partition, 35-split rank procedure and thresholds remain unchanged:

- top-half fraction at least 0.80;
- median percentile rank at least 0.60.

The window is determined solely by common source availability and complete-quarter boundaries. It is not selected from returns, rankings, bootstrap results, or verification outcomes.

## Preserved boundaries

- No v6.3 market outcome existed when this addendum was frozen.
- Verification years remain 2021-2025 and untouched by this correction.
- Material, bootstrap, DSR, rank, concentration, delay, cost and drawdown gates are unchanged.
- v6.1 and v6.2 rejection evidence remains unchanged.
- Paper-only, long-or-cash, no live or continuous-paper authorization.
