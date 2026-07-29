# Track A alpha discovery v0.6

This track searches for genuinely different paper-only alpha signals without changing the frozen v0.5 profitability gate, selection-stability policy, portfolio allocation, or meta-selection logic.

## Frozen boundary

The branch starts from commit `1027a771f5dbc1bbbdff277b4a0175eb25d81c89`.

Track A does not modify:

- `src/tradebot/backtest/profit_quality_gate.py`;
- `src/tradebot/backtest/selection_stability.py`;
- the v0.5 profitability acceptance rules;
- `src/tradebot/backtest/portfolio_trader.py`;
- the separate `research/v0.6-meta-allocation` branch.

The workflow verifies these boundaries with `git diff --exit-code` before running experiments.

## Strategy families

Track A contains exactly three families.

### 1. Multi-timeframe trend with pullback or continuation

The strategy requires alignment between fast, medium and slow completed-candle averages, a positive slow-trend slope and controlled extension. It can enter after either:

- a completed pullback followed by recovery above the fast trend; or
- a completed continuation breakout above a prior high.

### 2. Volatility compression, breakout and retest

The strategy identifies a completed low-range, low-ATR compression, then requires a close above the range high. Entry occurs only after a completed retest or a tightly constrained continuation. The breakout level is calculated exclusively from candles before the breakout candle.

### 3. Cross-asset relative strength

BTC, ETH, SOL, XRP and ADA are ranked using long- and short-horizon returns with a volatility penalty. Each symbol is evaluated as an independent long-or-cash sleeve. Peer candles after the signal timestamp are ignored. This adds cross-asset information without introducing portfolio allocation or meta-selection.

## Research protocol

The workflow uses:

- BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT and ADAUSDT;
- the latest 365 daily candles for every asset;
- 180 completed candles for training;
- the following 60 candles as an untouched unseen window;
- three non-overlapping unseen windows per asset, or 15 asset-periods per family;
- existing next-open paper execution;
- existing fees, slippage and tax estimates;
- the existing v0.5 execution profiles and temporal-stability screen.

Parameters are selected only on training data. Temporal folds inside the training window can force an abstention. No unseen metric participates in candidate ranking or family selection.

## Comparisons

Every Track A period records:

- cash return;
- buy-and-hold over the same unseen window;
- stability-aware momentum, breakout and mean-reversion results;
- a v0.5 reference strategy chosen only from training stability evidence.

A reference is never selected using unseen performance.

## Promising-candidate rule

A family is marked promising only when all of the following hold:

- average unseen net return after all modelled costs is positive;
- at least half of deployed unseen periods are profitable;
- at least six independent unseen periods deploy;
- average return beats cash and the training-selected v0.5 reference;
- at least two assets have positive average unseen return;
- worst unseen drawdown does not exceed 20%.

These are Track A reporting criteria. They do not replace or weaken the protected v0.5 profitability acceptance rules.

## Failed experiments

The workflow writes two primary JSON artifacts:

- `alpha_discovery.json` contains every family, unseen period, abstention, selected training configuration, benchmark and rejection reason;
- `alpha_candidate_diagnostics.json` contains every training candidate, its metrics, rank, temporal-fold diagnostics and stability rejection reasons.

A workflow can succeed while every family is rejected. Rejection is an honest research result, not a software failure.

## Safety

Everything remains paper-only and long-or-cash. The track cannot place orders, access wallets, store exchange credentials, use leverage or authorize real-money trading.
