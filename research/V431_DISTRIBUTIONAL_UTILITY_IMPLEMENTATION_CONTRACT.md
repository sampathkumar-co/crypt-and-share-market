# v4.3.1 Distributional Utility Implementation Contract

Status: frozen before any v4.3 model fit or sealed-period outcome.

## Training windows

Training ends 2025-06-30. For every model family the three fixed recency masks are:

- full: every valid training row;
- 720-day: rows dated on or after 2023-07-12;
- 360-day: rows dated on or after 2024-07-06.

The dates are inclusive and derived by subtracting 719 and 359 calendar days from the training end. A specialist recency model requires at least 250 rows bearing that true regime. A specialist is available only when at least two recency models fit.

Regime classifiers require at least 500 rows. Single-class classifier windows use a constant probability model rather than failing.

## Models and probability alignment

Each available specialist recency member fits four histogram-gradient-boosting regressors with the selected configuration:

- squared-error 3-day return;
- squared-error 7-day return;
- quantile-loss 3-day return with quantile 0.20;
- squared-error 3-day percentile rank.

Regime classifiers predict classes 0 chop, 1 trend, 2 panic and 3 recovery. Every classifier probability matrix is expanded to all four classes before averaging, with zero assigned to absent classes.

If averaged panic probability is below its threshold, regime selection is the highest averaged probability among available non-panic specialists. Ties use the lowest numeric regime code.

## Uncertainty

For a selected specialist, disagreement is:

`sqrt(std(expected_3d)^2 + std(expected_7d)^2 + std(q20_3d)^2 + (0.01 * std(rank))^2 + (0.01 * std(selected_regime_probability))^2)`

Standard deviations are calculated across the available recency members. The calibration disagreement threshold is separately selected as the 75th or 90th percentile of finite calibration-row disagreement.

Candidate ordering is descending predicted rank, then descending utility, then ascending asset symbol.

## Calibration tie break

Ties in calibration score prefer, in order:

1. lower maximum drawdown;
2. lower turnover;
3. higher utility threshold;
4. higher lower-tail floor;
5. lower panic threshold;
6. lower disagreement percentile;
7. top one before top two;
8. lower learning rate;
9. fewer leaves.

## Exact verification execution

The five sealed windows are simulated independently from 100% cash. Entry and scheduled rebalance targets use 5% of pre-trade equity per selected asset. Panic exits only nonzero holdings; repeated panic while already in cash does not restart cadence.

Terminal liquidation pays cost, affects maximum drawdown, and does not count as a target-changing action. Target exposure is capped at 10%; post-trade cost and natural market drift are reported separately rather than silently clipped.

No sealed-window label, return, trade, threshold, or summary may influence model selection. The entire sealed evaluation is executed once after tests and serialization pass.
