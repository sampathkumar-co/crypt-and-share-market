# v6.3.4 Common-Source Signal-Lag Addendum

Status: frozen after the corrected v6.3 workflow again failed closed, before producing any v6.3 candidate report or market outcome.

## Defect discovered

The v6.3.3 common-source discovery window began on 2020-07-01. The frozen strategy uses a one-day signal lag, so the first evaluated return on 2020-07-01 requires a source feature for 2020-06-30. The frozen Coinbase common-source feature panel begins on 2020-07-01, therefore that lagged feature is unavailable. The workflow failed closed before calculating rank stability, bootstrap results, or any v6.3 outcome.

## Frozen correction

Only the first common-source evaluation day is removed:

- adjusted 2020-Q3 common interval: 2020-07-02 through 2020-09-30;
- unchanged 2020-Q4 interval: 2020-10-01 through 2020-12-31.

This produces 183 aligned chronological observations for the dual-source candidate and every one of the 16 frozen comparison members. The one-day signal lag, model members, costs, execution, eight partitions, 35 CSCV-style rank splits, and all thresholds remain unchanged.

This correction is determined solely by causal feature availability. It is not selected from returns, rankings, bootstrap results, or the 2021-2025 verification outcomes.

## Preserved boundaries

- No v6.3 market outcome existed when this addendum was frozen.
- Verification years remain 2021-2025.
- Material, bootstrap, DSR, rank, concentration, delay, cost and drawdown gates are unchanged.
- v6.1 and v6.2 rejection evidence remains unchanged.
- No holdout is reopened or rewritten.
- Paper-only, long-or-cash, no live or continuous-paper authorization.
