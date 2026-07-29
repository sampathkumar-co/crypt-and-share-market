# Crypto Factor Risk Overlay v1.1 Protocol

This is a paper-only follow-up discovery experiment. It cannot authorise continuous paper or real-money trading because the v1.0 dataset has already been observed.

## Evidence-based hypothesis

The v1.0 ablation found that the simple trend portfolio produced much stronger returns but excessive drawdown, while the full multifactor ranker sharply reduced drawdown but diluted alpha. Therefore v1.1 keeps the simple trend logic unchanged as the alpha and ranking layer and uses the additional factors only to control exposure, position weights and risk.

## Frozen data and split

- BTCUSDT, ETHUSDT, SOLUSDT, DOGEUSDT and ADAUSDT.
- Exactly 1,800 aligned daily Coinbase candles.
- The same pre-registered v1.0 split: 240-bar warm-up, six 120-day early tests, 30-day embargo, six 120-day late tests and final 90-day embargo.
- This reuse is explicitly labelled secondary discovery, not independent validation.
- Completed candles generate targets; transactions occur at the next daily open.

## Frozen alpha layer

The alpha layer is identical to the v1.0 `simple_trend` baseline:

1. At least 60% of assets must have positive 30-day and 90-day return and trade above their 120-day average.
2. Median 90-day cross-sectional return must be positive.
3. BTC must satisfy the same positive trend conditions.
4. Eligible assets are ranked by blended 30-day and 90-day return divided by 30-day realised volatility.
5. The primary portfolio may hold the two strongest eligible assets.

The follow-up does not change those entry or ranking rules.

## Frozen factor-risk overlay

For the trend-selected assets only:

- Compute the committed v1.0 full factor score from momentum, trend quality, range position, volume confirmation, up/down volume, liquidity, realised volatility, downside volatility, drawdown, short-term overextension, BTC beta and correlation.
- Convert the average selected full-factor score into a mild exposure multiplier `0.55 + 0.45 * score`; factors can reduce but never increase exposure above the trend allocation.
- Scale individual inverse-volatility weights by `0.50 + 0.50 * factor_score`.
- Target bounded portfolio realised volatility.
- Reduce exposure when selected assets are highly correlated.
- Apply the existing prior-equity drawdown brake before every rebalance.
- Preserve minimum cash, per-asset caps, trade-size threshold, fees, slippage and tax assumptions.
- No leverage, shorting, derivatives, credentials, wallets or order APIs.

## Pre-registered variants

1. `primary_factor_risk`: top two, 75% maximum exposure, 25% minimum cash, 25% target volatility and 40% per-asset cap.
2. `conservative_factor_risk`: top two, 60% maximum exposure, 40% minimum cash, 18% target volatility and 32% cap.
3. `diversified_factor_risk`: top three, 70% maximum exposure, 30% minimum cash, 22% target volatility and 30% cap.
4. `risk_only_ablation`: identical primary volatility, correlation, cash and drawdown controls but no full-factor quality multiplier.
5. `raw_simple_trend`: the unchanged v1.0 simple trend baseline.

## Fail-closed discovery gate

The primary factor-risk overlay must:

- have positive average, compounded, extra-cost-stressed, early-half and late-half returns;
- trade in at least eight periods;
- have positive median return across active periods and at least 55% profitable active periods;
- keep worst period drawdown no greater than 15%;
- retain at least 60% of the raw simple-trend average return;
- improve the average-return-to-worst-drawdown ratio over raw simple trend by at least 20%;
- demonstrate factor value beyond risk controls by either beating the risk-only average return, or retaining at least 95% of risk-only return with at least 10% lower drawdown;
- have at least two positive factor-risk variants; and
- remain positive in at least four of five leave-one-asset-out runs.

Passing authorises only a new independent asset-universe replication. It does not authorise forward paper or live trading. A failed result remains evidence and must not be tuned after inspection under the same version.
