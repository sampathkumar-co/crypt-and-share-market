# v6.1 Parameter-Neighborhood Consensus Ensemble

Status: frozen after v6.0.3 diagnosed parameter instability and before any v6.1 ensemble outcome is calculated.

## Objective

Test one deterministic structural response to the v3.1 family’s 28.57% PBO and slightly negative block-bootstrap lower bounds: replace reliance on one chosen parameterization with an equal-weight ensemble of the complete frozen 10-day, 10%-exposure neighborhood.

This is a retrospective diagnostic on already exposed 2021-2025 dates. It cannot be called an untouched or forward breakthrough and cannot authorize trading.

## Frozen members

Use exactly the 16 existing v3.1 grid members satisfying:

- `rebalance_days = 10`;
- `maximum_exposure = 0.10`;
- both SMA lengths: 100 and 200;
- both `top_n` values: 1 and 2;
- both volatility targets: 0.02 and 0.03;
- both drawdown brakes: 0.10 and 0.20.

No member is selected or weighted by verification performance. Every member receives weight `1/16`.

## Execution

Each member runs the corrected v3.1.2 scheduled-execution state machine independently:

- completed D-1 features for the standard path;
- entries and due rebalances only on its 10-day schedule;
- immediate risk-off exits remain allowed;
- natural drift between target-changing decisions;
- same prior-day-known yielding-cash return.

The tradable ensemble target is the arithmetic mean of the 16 member target weights by asset. Aggregate turnover is netted before costs. Total target crypto exposure can never exceed 10%; the remainder is yielding cash.

Member shadow states exist only to form future ensemble targets. They place no orders and do not create separate cost charges in the aggregate portfolio.

## Frozen evaluations

Run independently on the unchanged Binance and Coinbase histories for 2021-2025:

- standard 20-bps round-trip costs;
- stress 40-bps round-trip costs;
- one-additional-day signal delay;
- decision-interval contribution concentration;
- maximum drawdown, actions, annualized return and excess over cash;
- source-specific moving-block bootstrap with 20, 60 and 120-day blocks and 10,000 deterministic resamples.

## Material gates

Use the existing v6 gates without relaxation:

- annualized standard return >= 5%;
- annualized excess over yielding cash >= 2 percentage points;
- positive stress return;
- at least four of five standard years positive;
- at least three of five stress years positive;
- at least 30 target-changing actions;
- maximum drawdown <= 5%;
- conservative delayed excess over cash > 0;
- maximum positive decision-interval share <= 20%;
- maximum positive year share <= 50%;
- both exchange paths reproduce and remain linked.

## Statistical gates

- all six source-specific block-bootstrap lower 95% bounds must be positive;
- DSR uses the lower genuine-source Sharpe, 225 direct-lineage trials (the frozen 224 floor plus this one ensemble), and the v6.0.3 corrected-grid Sharpe dispersion `0.17603369374678823`;
- DSR probability must be >= 0.95;
- no new parameter search is permitted.

The v6.0.3 PBO failure remains preserved. Because v6.1 is one predetermined ensemble rather than a selected grid winner, it reports a 35-split ensemble rank-stability audit against its 16 members instead of claiming that the old family PBO disappeared.

## Rank-stability audit

On corrected Binance discovery returns, include the fixed ensemble and its 16 members. Across the same 35 eight-partition CSCV splits, record the ensemble’s out-of-sample percentile rank without selecting a winner. Require the ensemble to rank in the top half in at least 80% of splits and its median percentile rank to be at least 0.60.

## Outcomes

- `ENSEMBLE_REJECTED`
- `RETROSPECTIVE_ENSEMBLE_CANDIDATE_FORWARD_REQUIRED`

Even a passing result remains retrospective and must be frozen into a new untouched forward programme; it does not modify v3.3 or authorize live/continuous paper trading.
