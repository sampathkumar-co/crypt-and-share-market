# v4.0 Learned Multi-Asset Reasoning Protocol

Status: frozen before any v4.0 historical or live paper outcome is calculated.

## Objective

Build a paper-only learned crypto decision system that can combine conflicting numerical evidence, rank multiple liquid assets, estimate downside and uncertainty, and remain in cash when evidence is weak.

The target is not a guaranteed return. Promotion requires at least 5% annualized net historical return after costs, five independent positive verification windows, cross-source replication, acceptable drawdown, and later forward paper evidence.

## Asset universe

The universe is fixed to:

- BTC-USD;
- ETH-USD;
- SOL-USD;
- XRP-USD;
- ADA-USD.

Only assets with complete required candles and acceptable recent quote-volume evidence may be eligible. Missing data fails closed.

## Data and chronology

- Provider: Coinbase Exchange public API.
- Candle size: five minutes.
- Training lookback: the latest 21 complete UTC days available before a run.
- Current-market smoke tests use only completed five-minute candles and public current prices.
- Feature rows use information available at candle close.
- Labels begin after the feature candle and cover the next six completed candles, a 30-minute horizon.
- Historical simulation fills at the next candle open.
- No random train/test splits are permitted.

## Learned components

The implementation uses numerical machine learning only; no paid AI or LLM API is required.

1. Regime classifier: predicts trend, chop, panic, or recovery from BTC and market-wide features.
2. Return ensemble: predicts each asset's 30-minute excess return after the market component.
3. Downside ensemble: estimates the probability that the future path breaches a frozen loss threshold.
4. Cross-asset ranking layer: ranks eligible assets using predicted return, downside probability, liquidity, regime compatibility, and uncertainty.
5. Uncertainty layer: uses disagreement across independently seeded learners and rejects unstable predictions.

The first implementation may use scikit-learn histogram gradient boosting models. Any replacement model requires a new protocol version.

## Frozen features

Per asset:

- 5, 15, 30, 60, and 240-minute returns;
- 30 and 120-minute realized volatility;
- candle range and close location;
- volume z-score and recent quote-volume proxy;
- short and medium trend efficiency;
- relative strength versus the equal-weight market;
- rolling beta and correlation to BTC.

Market-wide:

- BTC returns and volatility;
- equal-weight breadth;
- cross-sectional return dispersion;
- fraction of assets above their short moving average;
- median volume shock.

No news, social-media, or LLM-generated feature is permitted in v4.0. Such features require an ablation-tested later protocol.

## Model selection and validation

- Use the earliest 70% of labelled rows for training.
- Use the next 15% for calibration and fixed decision-threshold selection.
- Keep the final 15% untouched for the first historical verification.
- Hyperparameters are a small preregistered grid of no more than 12 combinations.
- Selection maximizes calibration net return subject to drawdown and turnover penalties.
- The untouched test set is evaluated once after the selected configuration is frozen.

## Decision and risk governor

- Long-or-cash only.
- At most two assets may be selected.
- Maximum target weight per asset: 5%.
- Maximum total crypto exposure: 10%.
- Minimum cash: 90%.
- No leverage, shorts, derivatives, lending, or averaging down.
- Reject an asset unless predicted net return is positive after stress costs.
- Reject an asset when downside probability exceeds 45%.
- Reject an asset when ensemble uncertainty exceeds the calibration threshold.
- Reject all exposure in panic regime.
- Maximum one target-changing decision per completed five-minute candle.

## Costs

- Standard round-trip cost: 20 basis points.
- Stress round-trip cost: 40 basis points.
- Live paper marks use public prices only and never place orders.

## Ten-minute current-market smoke loop

After tests pass, a live paper smoke loop may run for ten minutes:

- poll current public prices every 30 seconds;
- refresh completed five-minute candles when a new candle closes;
- generate reasoning, ranking, targets, and uncertainty;
- simulate fills and mark-to-market in an isolated fictional ledger;
- record data latency, prediction stability, action changes, costs, equity, and errors.

A ten-minute run validates plumbing and live behaviour only. It cannot establish profitability. Failures found in a smoke loop may be fixed and retested, but model thresholds or labels may not be tuned to the ten-minute P&L.

## Breakthrough gate

v4.0 is a historical breakthrough candidate only if all conditions pass:

1. untouched net annualized return is at least 5% at standard costs;
2. stress-cost net return is positive;
3. maximum drawdown is no more than 10%;
4. five independent walk-forward verification windows are positive at standard costs;
5. at least four are positive at stress costs;
6. profit is not dominated more than 70% by one asset or one window;
7. turnover and data completeness gates pass;
8. the result reproduces on an independent price source before forward promotion.

No ten-minute smoke result can satisfy this gate.

## Safety and isolation

- paper-only;
- `authorizes_trading=false`;
- `authorizes_shadow_paper=true` only for the isolated fictional v4.0 ledger;
- no credentials, wallets, exchange orders, leverage, or live capital;
- no modification of Track A, v3.1.2, v3.2, v3.3, or their evidence;
- v4.0 cannot replace the frozen v3.3 baseline without separate promotion evidence.
