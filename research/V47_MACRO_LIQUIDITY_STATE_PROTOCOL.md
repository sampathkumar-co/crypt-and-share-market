# v4.7 Macro-Liquidity State Protocol

## Objective

Test whether independent, prior-day-known macro-liquidity information can improve the verified v4.4 yield-bearing-cash baseline without changing its crypto model, exposure, cadence, costs, or long-or-cash boundary.

v4.7 is not another retuning of funding, basis, open interest, flow, volatility, breadth, or disagreement thresholds. It introduces a separate date-level macro gate trained only from public daily macro series.

## Frozen baseline

The trading baseline remains v4.4:

- the exact v4.3 learned bundle and hard regime routing;
- completed crypto candles and next-open fills;
- 3-day rebalance cadence;
- 5% per selected asset;
- standard one-way cost 10 bps;
- stress one-way cost 20 bps;
- prior-day-known DGS3MO yield on idle cash;
- paper-only, long-or-cash, no live execution.

## Independent data family

The macro history is downloaded from public FRED CSV endpoints with a fixed requested range from 2022-01-01 through 2026-06-30:

- `VIXCLS`: CBOE Volatility Index;
- `DTWEXBGS`: Nominal Broad U.S. Dollar Index;
- `DGS10`: 10-Year Treasury Constant Maturity Rate;
- `NASDAQCOM`: Nasdaq Composite.

Raw bytes, SHA-256 digests, observation counts, first dates, and last dates must be recorded for every series.

No observation dated on the crypto decision day may be used. For crypto date `D`, every macro value and every lookback endpoint must be selected from observations dated no later than `D - 1 day`.

## Macro features

Features are date-level and repeated across the five crypto assets. They contain no asset identity and cannot rank one crypto asset above another.

The fixed feature set includes:

- VIX level relative to its trailing 60-observation mean;
- VIX 5-, 20-, and 60-calendar-day changes;
- broad-dollar 5-, 20-, and 60-calendar-day changes;
- 10-year-yield level and 5-, 20-, and 60-calendar-day changes;
- Nasdaq 5-, 20-, and 60-calendar-day returns;
- a fixed risk-on composite combining Nasdaq strength with inverse VIX, dollar, and yield pressure.

Three predeclared families are evaluated:

1. `risk_appetite`: VIX and Nasdaq features.
2. `dollar_rates`: broad-dollar and Treasury-yield features.
3. `full_macro`: all macro features including the composite.

## Model and action boundary

Each fold fits a small regularized logistic classifier to predict whether the average cross-asset forward 3-day return is positive. Training is date-level, not asset-row-level.

The gate receives only the macro-family features. It does not receive crypto prices, predicted utilities, ranks, funding, basis, open interest, flows, asset identity, or sealed-window outcomes.

The gate may only replace a non-panic baseline target with cash when its probability is below the calibrated threshold. It must never:

- add an asset;
- replace the baseline-selected asset with another asset;
- increase `top_n`;
- increase target exposure;
- act outside the frozen rebalance cadence;
- override a baseline panic-to-cash decision.

## Walk-forward selection

Use the six v4.6 walk-forward folds. For each fold:

1. independently train and calibrate the frozen v4.3 base bundle using only that fold's allowed history;
2. fit each macro-family classifier using dates no later than the fold training end;
3. choose that family's gate threshold using only the fold's base-calibration quarter;
4. evaluate the frozen family/threshold on the following validation quarter.

A macro family is eligible only if:

- compounded validation excess versus its fold baselines is positive;
- at least four of six folds have strictly positive excess;
- no fold excess is below -0.25%;
- aggregate actions and turnover do not exceed baseline;
- no fold drawdown exceeds baseline drawdown by more than 0.25%;
- at least one validation trade is actually gated.

The disabled macro gate is always an eligible fallback and reproduces v4.4 exactly.

## Final retrospective evaluation

After family selection is frozen:

1. use the final frozen v4.3 bundle through 2025-06-30;
2. fit the selected macro-family classifier only through 2025-06-30;
3. choose its threshold only on 2025-07-01 through 2025-09-30;
4. evaluate the five already exposed sealed windows from 2025-10-01 through 2026-06-30.

All original profitability, diversity, concentration, cost, and drawdown gates remain unchanged. The report must explicitly mark the result retrospective and `untouched_historical_dates=false`.

## Acceptance

v4.7 is a historical breakthrough only if the original frozen gates pass. Independent-source replication and current-market smoke remain false until separately completed.

If no macro family survives walk-forward eligibility, v4.7 must select the disabled gate and record that the independent macro family did not improve the baseline robustly.
