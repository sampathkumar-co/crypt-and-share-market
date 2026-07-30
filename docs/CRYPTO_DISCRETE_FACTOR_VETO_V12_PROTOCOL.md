# Crypto Discrete Factor Veto v1.2 Protocol

This is a paper-only cross-universe replication experiment. It cannot authorise live trading or continuous paper trading.

## Evidence-based hypothesis

v1.1 showed that factor and volatility controls materially reduced drawdown and improved return-to-drawdown efficiency, but continuous factor scaling retained only 44% of raw trend return. v1.2 therefore preserves the raw trend allocation by default and uses factors only as a discrete defensive veto when multiple independent risk families deteriorate together.

## Frozen universe and data

- LTCUSDT, BCHUSDT, LINKUSDT, XLMUSDT, ETCUSDT, ATOMUSDT, UNIUSDT and AAVEUSDT.
- Exactly 2,050 aligned Coinbase daily candles per asset.
- The first 1,800 candles form the discovery block.
- The latest 250 candles are a barred holdout and must not be evaluated by v1.2.
- The discovery block keeps the original split: 240-bar warm-up, six 120-day early tests, 30-day embargo, six 120-day late tests and final 90-day embargo.
- Completed candles generate targets; transactions occur at the next daily open.

Only data availability and timestamp coverage were inspected before this protocol. No return from the new universe was calculated.

## Frozen alpha layer

The alpha and ranking layer remains the v1.0 simple trend method:

1. At least 60% of assets must have positive 30-day and 90-day returns and trade above their 120-day average.
2. Median 90-day cross-sectional return must be positive.
3. The highest-dollar-volume market proxy must satisfy the same trend condition.
4. Eligible assets are ranked by blended 30-day and 90-day return divided by 30-day realised volatility.
5. The primary portfolio holds the two strongest eligible assets.

## Frozen discrete veto

The raw trend target is preserved when fewer than two risk families are active. Risk families are:

1. **Factor quality:** average selected full-factor score below 0.50.
2. **Tail risk:** any selected asset has a 90-day drawdown of at least 25%, or selected average 60-day downside volatility is at least 90% annualised.
3. **Crowding:** selected pair correlation is at least 0.90, or average universe correlation is at least 0.88.
4. **Fragility:** selected median volume confirmation is below -10%, or selected average seven-day overextension exceeds 10%.

Actions:

- Zero or one active risk family: preserve the raw trend exposure.
- Two active families: cap exposure at 55%.
- Three or four active families: cap exposure at 30%.
- Crisis veto: hold cash when the market proxy drawdown is at least 35% or median universe volatility is at least 140% annualised.
- The existing prior-equity drawdown brake remains active.
- Relative weights stay the raw inverse-volatility trend weights; factor scores do not continuously rescale assets.
- No leverage, shorting, derivatives, wallets, credentials or order APIs.

## Pre-registered variants

1. `primary_discrete_veto`: top two, raw 80% maximum exposure and 40% asset cap.
2. `conservative_discrete_veto`: top two, 70% maximum exposure and 35% cap.
3. `diversified_discrete_veto`: top three, 80% maximum exposure and 30% cap.
4. `continuous_factor_risk`: unchanged v1.1 primary continuous factor-risk overlay.
5. `continuous_risk_only`: unchanged v1.1 risk-only overlay.
6. `raw_simple_trend`: unchanged simple trend baseline.

## Fail-closed discovery gate

The primary discrete veto must:

- exactly reproduce the raw trend reference in its raw arm;
- have positive average, compounded, extra-cost-stressed, early-half and late-half returns;
- trade in at least eight periods;
- have positive median return across active periods and at least 55% profitable active periods;
- keep worst period drawdown no greater than 20%;
- retain at least 65% of raw trend average return;
- improve average-return-to-worst-drawdown efficiency over raw trend by at least 20%;
- beat the continuous factor-risk average return;
- retain at least 95% of continuous risk-only average return;
- reduce raw trend drawdown by at least 25%;
- have at least two positive discrete-veto variants;
- select at least four distinct assets across discovery periods; and
- remain positive in at least six of eight leave-one-asset-out runs.

Passing authorises only an unchanged v1.3 evaluation on the barred 250-candle holdout. v1.2 must never inspect holdout returns. A rejected result remains evidence and must not be retuned on this discovery block.
