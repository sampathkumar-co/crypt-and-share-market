# Crypto Multifactor Portfolio v1.0 Protocol

This experiment remains paper-only and cannot authorise real-money trading.

## Hypothesis

A regime-aware cross-sectional portfolio using several independent price, volume, liquidity and risk factors can outperform the rejected simple trend portfolio after fees, slippage and estimated crypto taxes.

## Frozen universe and data

- BTCUSDT, ETHUSDT, SOLUSDT, DOGEUSDT and ADAUSDT.
- Exactly 1,800 aligned daily candles per asset from public Coinbase spot data.
- Signals use completed candles; rebalances execute at the next daily open.
- Split: 240-bar feature warm-up, six 120-day early tests, 30-day embargo, six 120-day late tests and a final 90-day embargo.

## Frozen factors

The full score uses cross-sectional ranks and absolute eligibility checks for:

1. 14-day momentum.
2. 60-day momentum.
3. 180-day momentum excluding the most recent seven days.
4. 120-day trend slope and R-squared quality.
5. Distance above the 100-day moving average.
6. Position within the 90-day high-low range.
7. Twenty-versus-ninety-day volume confirmation.
8. Up-day versus down-day volume participation.
9. Thirty-day median dollar-volume liquidity.
10. Thirty-day realised volatility.
11. Sixty-day downside volatility.
12. Ninety-day peak-to-trough drawdown.
13. Seven-day overextension penalty.
14. Correlation and beta to BTC.

## Regime and allocation

- Market state uses BTC trend, breadth, median momentum, cross-asset correlation and realised volatility.
- Crisis and bear regimes hold cash.
- Neutral regimes use reduced exposure; healthy bull regimes may use up to 75% exposure.
- Hold at most three assets, cap each weight, preserve a cash reserve and target bounded portfolio volatility.
- Reject redundant highly correlated leaders and small rebalance changes.
- No leverage, shorting, derivatives, wallets or order APIs.

## Pre-registered comparisons

- Full multifactor primary.
- Conservative full multifactor variant.
- Price-only ablation.
- Rejected v0.8-style simple trend baseline on identical periods.
- Cash, BTC buy-and-hold and equal-weight five-asset buy-and-hold.
- Extra turnover-cost stress and leave-one-asset-out reruns.

## Fail-closed acceptance

Forward-paper candidacy requires positive average, median and compounded full-period return; positive averages in both early and late halves; at least seven profitable periods and eight active periods; positive extra-cost-stressed return; improvement over both price-only and simple-trend baselines; controlled drawdown; at least two positive multifactor variants; and positive average returns in at least four of five leave-one-asset-out runs.

A build or workflow success is not a profitability pass. Failed results remain evidence and must not be merged as an approved strategy.
