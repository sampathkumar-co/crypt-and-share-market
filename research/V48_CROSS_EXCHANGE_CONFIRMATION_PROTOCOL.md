# v4.8 Cross-Exchange Confirmation Protocol

## Research question

Can completed Coinbase BTC/USD and ETH/USD daily price/liquidity information improve the frozen Binance-derived v4.4 strategy when it is supplied directly to the learned regime and ranking models?

## Motivation

v4.6 showed that vetoing the same distributional outputs does not generalize. v4.7 showed that coarse macro exposure scaling does not generalize. v4.8 therefore tests a new information channel inside the model rather than another post-model rule.

## Control

Every walk-forward validation compares against an independently refitted v4.3 control with v4.4 prior-day-known DGS3MO cash accounting.

The final control is the exact frozen v4.4 report and bundle.

## Independent source

Use Coinbase Exchange public daily candles for:

- `BTC-USD`
- `ETH-USD`

Fixed source interval: 2021-01-01 through 2026-06-30.

Requests use UTC daily candles in chunks of no more than 250 days. Raw response hashes, URLs, product IDs, requested ranges and parsed row counts must be archived.

All required BTC and ETH dates must be present. Missing or conflicting candles fail closed.

## Timing rule

A Coinbase candle for date `D` may be used only as a feature for the completed date-`D` crypto row. Execution remains at the next Binance bar open.

No date after `D` may affect date-`D` features. Labels, fills and sealed windows are unchanged.

## Feature families

### Price confirmation

Market-level features calculated from Coinbase BTC/ETH and the matching Binance spot series:

- Coinbase returns over 1, 7 and 30 days for BTC and ETH
- Coinbase two-asset market returns over 7 and 30 days
- Coinbase-minus-Binance return divergence over 1, 7 and 30 days for BTC and ETH
- BTC and ETH USD-versus-USDT close premium
- premium changes over 1 and 7 days
- cross-exchange momentum sign agreement over 7 and 30 days

### Liquidity confirmation

- Coinbase quote-volume changes over 1, 7 and 30 days for BTC and ETH
- Coinbase share of Coinbase-plus-Binance spot quote volume for BTC and ETH
- volume-share changes over 1 and 7 days
- aggregate Coinbase volume share
- aggregate volume-share changes over 1 and 7 days
- Coinbase BTC/ETH liquidity breadth over 7 and 30 days

### Combined

All price-confirmation and liquidity-confirmation features.

All added features are market context and are repeated across the five asset rows for the same date. The original v4.3 feature columns remain unchanged and precede the added columns.

## Candidate set

Exactly four candidates:

1. disabled baseline: original v4.3 features
2. price confirmation
3. liquidity confirmation
4. combined confirmation

Each active candidate is a separately trained model family. There is no post-sealed feature removal or threshold tuning.

## Walk-forward selection

Use the six v4.6 folds. For every fold:

1. independently fit/calibrate the original control bundle
2. independently fit/calibrate each active augmented bundle using identical model/calibration grids
3. evaluate the following validation quarter under standard and stress costs with v4.4 cash yield

An active family is eligible only when:

- at least four of six standard-cost folds have positive excess versus control
- compounded standard excess is positive
- compounded stress excess is positive
- worst standard fold excess is no worse than -0.50%
- no validation drawdown exceeds control by more than 0.50%
- maximum target exposure remains at or below 10%

If no active family is eligible, select the disabled v4.4 baseline.

Among eligible families prioritize:

1. worst standard fold excess
2. positive standard fold count
3. compounded stress excess
4. compounded standard excess
5. lower maximum drawdown
6. lower turnover
7. fewer added features

## Final training and sealed evaluation

After the feature family is selected:

- disabled: reproduce exact v4.4
- active: train/calibrate one final augmented bundle using the frozen v4.3 training and calibration periods, then evaluate the five existing exposed sealed windows once

The profitability gates, costs, cadence, labels, universe and long-or-cash constraint remain unchanged.

## Safety

Retrospective and paper-only. `authorizes_trading=false`. Historical sealed dates are already exposed and must remain marked `untouched_historical_dates=false`.
