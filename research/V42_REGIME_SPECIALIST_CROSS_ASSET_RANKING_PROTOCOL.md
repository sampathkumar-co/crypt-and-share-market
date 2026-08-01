# v4.2 Regime-Specialist Cross-Asset Ranking Protocol

Status: frozen before any v4.2 model is trained or any v4.2 outcome is accessed.

## Purpose

v4.1 produced a small positive untouched return with low drawdown and adequate activity, but it failed the 5% annualized and five-positive-window gates. Its weakness was not cost leakage or concentration; the independent absolute-return regressors did not rank opportunities consistently across regimes.

v4.2 is a genuinely different learned mechanism:

- cross-sectional ranking rather than independent return thresholding;
- separate trend, recovery, and defensive/chop specialists;
- spot, perpetual, funding, open-interest, flow, and basis state;
- a meta-label that permits a ranked candidate only when its expected return clears stress costs and downside risk.

No v4.1 verification return, window, or current-market prediction may select a v4.2 parameter.

## Fixed universe

- BTCUSDT;
- ETHUSDT;
- SOLUSDT;
- XRPUSDT;
- ADAUSDT.

Long-or-cash only. At most two assets. Maximum 5% target per asset, 10% total crypto exposure, and 90% minimum cash.

## Fixed public sources

Binance public archives only for the first campaign:

- spot daily klines;
- USD-M perpetual daily klines;
- funding-rate history aggregated by UTC day;
- open-interest metrics aggregated by UTC day.

Every URL and raw SHA-256 is recorded. Only completed UTC days are used. Missing spot, perpetual, funding, or open-interest state fails the affected date closed. No interpolation across missing market dates.

## Fixed historical interval and five walk-forward verification folds

Required source coverage: January 1, 2021 through December 31, 2024.

1. Train 2021-01-01 through 2022-06-30; calibrate 2022-Q3; verify 2022-Q4.
2. Train 2021-01-01 through 2022-12-31; calibrate 2023-Q1; verify 2023-Q2.
3. Train 2021-01-01 through 2023-06-30; calibrate 2023-Q3; verify 2023-Q4.
4. Train 2021-01-01 through 2023-12-31; calibrate 2024-Q1; verify 2024-Q2.
5. Train 2021-01-01 through 2024-06-30; calibrate 2024-Q3; verify 2024-Q4.

Each verification quarter starts and ends in cash. Models are refit separately inside each fold. No later fold data may influence an earlier fold.

A separately frozen 2025-01-01 through 2026-06-30 campaign may be run only after all implementation and five-fold tests are complete. It is confirmatory evidence, not a replacement for future paper validation.

## Fixed features

Per asset, using completed data through day D:

- spot returns over 1, 3, 7, 14, 30, 60, and 120 days;
- perpetual returns over the same horizons;
- spot-versus-perpetual relative return;
- annualized basis level and changes over 1, 3, 7, and 30 days;
- funding level, 3-day and 7-day means, 30-day z-score, and sign persistence;
- open-interest changes over 1, 3, 7, and 30 days;
- price/open-interest interaction state: build-up, short build-up, long liquidation, or short covering;
- spot and perpetual quote-volume changes;
- taker-buy imbalance and spot-versus-perpetual flow divergence;
- 7, 30, and 90-day volatility;
- distance from 20, 50, 100, and 200-day moving averages;
- 14 and 60-day trend efficiency;
- 30 and 90-day beta and correlation to BTC;
- cross-sectional ranks of momentum, basis change, funding, open-interest change, volatility, and flow.

Market-wide:

- BTC trend and volatility state;
- equal-weight market returns and breadth;
- cross-sectional dispersion;
- median funding and open-interest change;
- average pairwise correlation;
- fraction of assets in long build-up, liquidation, and recovery states.

No news, social, LLM, manually entered, or future-derived feature is permitted.

## Learned targets

For every day D and asset:

- 3-day next-open-to-next-open absolute return;
- 7-day next-open-to-next-open absolute return;
- cross-sectional rank of 3-day net return after stress costs;
- probability the asset is in the top two and has positive 3-day stress-net return;
- probability of a path loss worse than 2% during the next three days;
- market regime: trend, recovery, chop, or panic.

## Regime specialists

Three separately trained ranking specialists are fixed:

- trend specialist: dates labelled trend;
- recovery specialist: dates labelled recovery;
- defensive specialist: dates labelled chop.

Panic always forces cash and has no long specialist.

The predicted regime selects exactly one specialist. A specialist ranks all five assets jointly. The meta-label and downside classifier may veto any ranked candidate.

## Fixed model family and grid

Histogram gradient boosting only. No neural network and no paid AI API.

Exactly four configurations:

1. learning rate 0.04, 15 leaves, 120 iterations;
2. learning rate 0.04, 31 leaves, 120 iterations;
3. learning rate 0.08, 15 leaves, 120 iterations;
4. learning rate 0.08, 31 leaves, 120 iterations.

Three fixed seeds per specialist, meta-label, downside, and regime target.

Calibration may choose only:

- meta-label probability threshold: 0.45, 0.55, or 0.65;
- maximum downside probability: 0.35 or 0.45;
- top one or top two assets;
- ensemble disagreement threshold: calibration 75th percentile.

Calibration score is standard net return minus twice maximum drawdown minus 0.25 times turnover. Fewer than eight costed actions receives a fixed penalty of one.

## Decision and execution

- Signal uses completed day D.
- Fill occurs at day D+1 UTC open.
- Primary holding/rebalance cadence is three complete open-to-open periods.
- Panic may exit to cash at the next daily open.
- A candidate must have positive predicted 3-day and 7-day return after the 40-basis-point stress round trip.
- Meta-label probability must clear the calibrated threshold.
- Downside probability and disagreement must remain below calibrated/frozen limits.
- Targets are 5% per selected asset.
- Natural drift is preserved between costed actions.
- Actual absolute traded notional pays cost.
- Every fold pays entry, rebalance, exit, and terminal-liquidation costs.

## Costs

- standard round trip: 20 basis points;
- stress round trip: 40 basis points.

No leverage, shorts, derivatives positions, lending, averaging down, or exposure escalation. Derivatives data is informational only.

## Five-fold breakthrough gate

A v4.2 historical breakthrough candidate requires all of the following:

1. all five verification quarters are positive at standard costs;
2. all five verification quarters are positive at stress costs;
3. aggregate annualized standard net return is at least 5%;
4. aggregate stress net return is positive;
5. maximum drawdown is no more than 10%;
6. at least 20 costed target-changing actions occur across the five quarters;
7. BTC and at least two non-BTC assets are selected;
8. no one asset, fold, or regime supplies more than 70% of positive contribution;
9. the exact selected mechanism later reproduces on an independent price source;
10. a current-market shadow smoke completes without data, serialization, execution, or liquidation errors.

Independent-source replication remains false in the first Binance campaign.

## Safety

- paper-only;
- `authorizes_trading=false`;
- isolated fictional shadow ledger only;
- no credentials, wallets, exchange orders, or capital;
- Track A, v3.1.2, v3.2, v3.3, and v4.1 evidence remain unchanged;
- v4.2 cannot replace the verified BTC/ETH baseline without independent replication and forward paper evidence.
