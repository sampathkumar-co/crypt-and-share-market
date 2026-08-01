# v4.1.1 Daily Implementation Contract

Status: frozen before any v4.1 model is trained or evaluated.

## Exact data interval

- Exactly 1,000 aligned completed UTC daily candles per asset.
- Earliest 200 candles are feature warm-up only.
- A feature row at completed day D may use data through D close.
- One-day target: D+1 open to D+2 open.
- Three-day target: D+1 open to D+4 open.
- Seven-day target: D+1 open to D+8 open.
- The final eight daily opens are reserved for labels and execution only.

## Fixed model grid

Four configurations only:

1. learning rate 0.04, 15 leaves, 120 iterations;
2. learning rate 0.04, 31 leaves, 120 iterations;
3. learning rate 0.08, 15 leaves, 120 iterations;
4. learning rate 0.08, 31 leaves, 120 iterations.

Each return and classifier target uses three fixed seeds. No neural network or LLM API is used.

## Fixed calibration choices

Calibration may choose only among:

- opportunity probability threshold: 0.35, 0.45, or 0.55;
- required positive horizons: exactly two or exactly three;
- ensemble disagreement threshold: calibration 75th percentile;
- liquidity threshold: calibration 10th percentile of the feature `quote_volume_30`.

The primary expected-return score is:

- 20% one-day expected return;
- 50% three-day expected return;
- 30% seven-day expected return.

The three-day downside probability penalty and uncertainty penalty remain fixed in the score. No untouched result may alter these weights.

## Exact simulation

- Portfolio state is marked daily from the next open to the following open.
- At a due rebalance, holdings are moved toward 5% target value per selected asset using the next open.
- Actual absolute traded notional pays the one-way cost.
- Holdings drift naturally on intervening days.
- The next scheduled rebalance occurs after three complete open-to-open holding periods.
- A predicted panic regime may exit to cash at the next daily open before the scheduled rebalance.
- No other early target change is permitted.
- Every verification segment starts in cash and pays terminal liquidation.

## Selection rules

An asset is eligible only when:

- the required number of horizons predict positive return after stress round-trip cost;
- seven-day expected return is positive;
- every required opportunity probability exceeds the selected calibration threshold;
- three-day downside probability is at most 45%;
- combined ensemble disagreement is at or below the calibration threshold;
- 30-day quote-volume proxy is at or above the calibration threshold;
- regime is not panic.

At most the two highest-scoring eligible assets are selected.

## Calibration score

Calibration maximizes:

`net_return - 2 * maximum_drawdown - 0.25 * turnover`

Configurations with fewer than eight target-changing actions receive a fixed penalty of one.

## Untouched gate

The final 15% is evaluated once with the chosen frozen bundle. The original v4.1 historical breakthrough and independent-replication gates remain unchanged.
