# v4.3 Distributional Utility Ranking Protocol

Status: frozen before any v4.3 model fit or sealed-period outcome is accessed.

## Purpose

v4.2 stayed in cash because independently trained meta and downside classifiers never cleared their hard intersection. The labels contained viable opportunities, but the classifiers were poorly aligned and fixed random seeds produced almost identical histogram models.

v4.3 is a distinct mechanism:

- direct expected-return and lower-tail forecasting instead of hard meta/downside classifier intersection;
- genuine ensemble diversity from nested historical training windows;
- probability-based panic control;
- regime-specialist cross-sectional ranking;
- calibration only on previously exposed development data;
- one-time evaluation on a still-sealed period.

No threshold is selected from October 2025 through June 2026 outcomes.

## Universe, sources and features

Universe: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT and ADAUSDT.

Public Binance spot, USD-M perpetual, funding and open-interest archives are used through 2026-06-30. v4.2 source aggregation, missing-date closure, raw hashes and exact 90-feature completed-day matrix are reused unchanged.

Long-or-cash only. Derivatives data is informational; no derivative position is created.

Development training uses valid rows through 2025-06-30. Calibration uses 2025-07-01 through 2025-09-30. All prior v4.2 folds are development history and are not claimed as untouched v4.3 evidence.

## Sealed verification windows

Each window starts and ends in cash:

1. 2025-10-01 through 2025-11-24;
2. 2025-11-25 through 2026-01-18;
3. 2026-01-19 through 2026-03-14;
4. 2026-03-15 through 2026-05-07;
5. 2026-05-08 through 2026-06-30.

## Learned mechanism

Three non-panic specialists are trained for chop, trend and recovery. Each predicts:

- 3-day expected absolute return;
- 7-day expected absolute return;
- 3-day 20th-percentile absolute return;
- 3-day cross-sectional return rank.

For genuine diversity, each specialist may contain models trained on:

- all available training rows;
- the latest 720 calendar days;
- the latest 360 calendar days.

A specialist requires at least two available recency models. Each recency model requires at least 250 regime rows.

Separate regime classifiers use the same three recency windows. Their aligned probabilities are averaged. Panic forces cash when averaged panic probability clears the calibrated threshold; otherwise the highest-probability available non-panic specialist is used.

## Candidate utility and calibration

For each asset:

`utility = 0.55 * expected_3d + 0.25 * expected_7d + 0.20 * q20_3d + 0.01 * (predicted_rank - 0.5) - 0.50 * disagreement`

A candidate must have expected 3-day and 7-day returns above the 40-basis-point stress round trip, clear the calibrated utility threshold, satisfy the calibrated lower-tail floor, and remain below the calibrated disagreement threshold.

Calibration may choose only:

- panic probability threshold: 0.45, 0.55 or 0.65;
- utility threshold: 0.004, 0.008 or 0.012;
- 3-day 20th-percentile floor: -0.03, -0.02 or -0.01;
- top one or top two assets;
- disagreement percentile: 75th or 90th;
- one of the original four histogram-gradient-boosting configurations.

Calibration score remains standard net return minus twice maximum drawdown minus 0.25 times turnover. Fewer than eight costed actions receives a fixed penalty of one.

## Execution and safety

Signals use completed day D and fill at day D+1 spot open. Scheduled target changes occur after three complete open-to-open periods. Panic may exit at the next open. Natural drift is preserved.

Target size is 5% per selected asset, maximum 10% total target exposure and minimum 90% cash. Actual traded notional pays costs. Standard round trip is 20 basis points and stress round trip is 40 basis points. Every window pays terminal liquidation.

Paper-only. `authorizes_trading=false`; no credentials, orders, wallets, leverage, shorts, lending or averaging down. No paid AI API is used.

## Breakthrough gate

A v4.3 historical candidate requires:

1. all five sealed windows positive at standard costs;
2. at least four of five sealed windows positive at stress costs;
3. aggregate annualized standard return at least 5%;
4. aggregate stress return positive;
5. maximum drawdown no more than 10%;
6. at least 20 costed target-changing actions;
7. BTC and at least two non-BTC assets selected;
8. no asset, window or regime supplies more than 70% of positive contribution;
9. independent-source replication;
10. a clean current-market shadow smoke.

The first Binance evaluation intentionally leaves replication and smoke false.
