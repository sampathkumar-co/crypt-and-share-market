# v4.9 Cross-Exchange Target Ensemble Protocol

## Research question

Can the positive aggregate information found by the v4.8 combined cross-exchange model be retained while bounding its bad-fold overtrading and undertrading by blending its target portfolio with the stable v4.4 control?

## Motivation

v4.8 combined confirmation produced:

- four positive standard-excess folds out of six
- approximately +1.00% compounded standard excess
- approximately +0.97% compounded stress excess

It failed because one fold lost approximately 0.93% versus control and another fold marginally exceeded the drawdown allowance. The losing folds failed in opposite directions, so a full replacement model is too unstable.

v4.9 does not relax that gate. It tests whether a fixed capital blend mechanically bounds either model's error.

## Models

For every walk-forward fold, independently fit and calibrate:

1. the original v4.3 control model on the original Binance feature set
2. the v4.8 combined model on the original features plus all 39 Coinbase/Binance price and liquidity confirmation features

Both use the unchanged v4.3 model grid and calibration procedure.

## Target ensemble

At each existing rebalance decision:

- the control model proposes its normal 5% target per selected asset
- the combined model proposes its normal 5% target per selected asset
- the portfolio target is the weighted sum of those two target portfolios

If both select the same asset, the target remains 5%.

If they select different assets, capital is split according to the fixed model weights.

If one model selects cash or enters panic, only that model's capital sleeve moves to cash.

The ensemble may react to each model's existing panic exit exactly as its source strategy does. It may not otherwise act off the existing three-day cadence.

## Candidate set

Exactly four candidates:

1. disabled v4.4 baseline
2. 25% combined / 75% control
3. 50% combined / 50% control
4. 75% combined / 25% control

No continuous weight fitting, regression stacking, sealed tuning or fold-specific final weight is allowed.

## Exposure and costs

- long-or-cash only
- same standard and stress transaction costs
- prior-day-known DGS3MO yield on shared idle cash
- maximum aggregate target exposure 10%
- transaction cost applies to net portfolio turnover after the two sleeves are combined

## Walk-forward selection

Use the six existing v4.6 folds. Each fold independently refits both source models before the following validation quarter.

An active weight is eligible only when:

- at least four of six standard-cost folds have positive excess versus the independently refitted v4.4 control
- compounded standard excess is positive
- compounded stress excess is positive
- worst standard fold excess is no worse than -0.50%
- no fold drawdown exceeds control by more than 0.50%
- maximum target exposure is at most 10%
- aggregate standard-cost turnover is at most 125% of control turnover
- no fold turnover exceeds 150% of control turnover plus 0.05 notional

If no active weight is eligible, select exact v4.4.

Among eligible weights prioritize:

1. worst standard fold excess
2. positive standard fold count
3. compounded stress excess
4. compounded standard excess
5. lower maximum drawdown
6. lower turnover
7. lower combined-model weight

## Final training and sealed evaluation

After the weight is selected:

- reuse the exact frozen v4.3 final control bundle
- independently train/calibrate one final combined-feature bundle using only the frozen v4.3 training and calibration periods
- evaluate the selected fixed target ensemble on the existing five exposed sealed windows once

If baseline is selected, reproduce exact v4.4 without training a final combined model.

The profitability gates, labels, universe, costs and sealed windows remain unchanged.

## Safety

Retrospective and paper-only. `authorizes_trading=false`. Historical sealed dates remain marked `untouched_historical_dates=false`.
