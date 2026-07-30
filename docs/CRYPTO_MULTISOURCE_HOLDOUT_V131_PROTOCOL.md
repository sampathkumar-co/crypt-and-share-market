# Crypto Multi-Source Holdout v1.3.1 Protocol

This is a transport-corrected, one-shot, paper-only evaluation on the same previously barred crypto interval. It cannot authorise real-money trading or continuous paper trading.

## Why v1.3.1 exists

The frozen v1.3 workflow passed all tests and exact sealed-price verification, but stopped before evaluation because Bybit returns HTTP 403 to US-hosted GitHub runners. No holdout return was calculated and the holdout remained unconsumed.

A metadata-only audit then checked alternative public derivatives sources without opening any price file:

- OKX and Bitget were reachable but exposed only roughly three months of funding history through their public endpoints, insufficient for the frozen warm-up.
- Hyperliquid's public `fundingHistory` endpoint listed LTC, BCH, LINK, XLM, ETC, ATOM, UNI and AAVE and returned records in both required coverage windows.
- The audit artifact fingerprint is `sha256:e3332f0eb9411ed0a86c573598d30efc9528d255e0aa3f440ffbda0c952aa4fd`.

v1.3.1 changes only the derivatives data source and retrieval transport. No factor rule, price input, date split, exposure mapping, comparison or acceptance threshold changes.

## Frozen universe and time split

- LTCUSDT, BCHUSDT, LINKUSDT, XLMUSDT, ETCUSDT, ATOMUSDT, UNIUSDT and AAVEUSDT.
- Reuse the exact 2,050 aligned Coinbase histories and price fingerprint from v1.2/v1.3.
- Evaluate only the first 180 candles of the previously barred 250-candle interval as three consecutive 60-day tests.
- The final 70 candles remain an embargo and are not evaluated.
- External-factor warm-up may use only dates before each execution candle.
- External daily observations remain conservatively lagged by one extra calendar day.

## Frozen alpha and non-derivatives factors

The inherited simple-trend alpha, stablecoin-liquidity family, Coin Metrics on-chain family and FRED macro-risk family remain byte-for-byte unchanged from v1.3.

## Transport-corrected derivatives family

- Source: Hyperliquid public `POST https://api.hyperliquid.xyz/info` with request type `fundingHistory`.
- Map the eight Coinbase symbols to Hyperliquid perpetual coin names LTC, BCH, LINK, XLM, ETC, ATOM, UNI and AAVE.
- Retrieve the exact frozen interval from 27 May 2025 through 21 May 2026.
- Paginate forward using the final returned timestamp because the endpoint returns at most 500 records per request.
- Preserve raw hourly settled funding observations, then aggregate to daily means inside the unchanged factor engine.
- Compute the unchanged selected-asset median seven-day daily funding mean and compare it with the preceding 90-day distribution.
- The derivatives family is supportive only when the current value is no higher than the 75th percentile.

The change from eight-hour Bybit settlements to hourly Hyperliquid settlements does not alter the factor definition because both are first normalized to one daily mean per asset.

## Frozen exposure mapping

- Fewer than two supportive families: cash.
- Exactly two supportive families: 50% of the inherited raw trend target.
- Exactly three supportive families: 75% of the inherited raw trend target.
- All four supportive families: preserve the inherited raw trend target.
- Existing prior-equity drawdown brake remains active.
- External factors cannot create an entry, alter trend ranking, or increase raw exposure.

## Frozen comparisons and gate

The primary four-family confirmation, four leave-one-family-out variants, raw inherited simple trend and cash comparison remain unchanged from v1.3.

The primary must still complete exactly three periods; have positive average, compounded and stressed return; profit in at least two periods; remain positive across the first-two and last-two period summaries; beat raw trend on average and in at least two periods; keep drawdown no greater than 15%; trade in at least two periods and at least three assets; and have at least three positive leave-one-family-out variants.

Passing authorises only a time-limited shadow-paper candidate and a later forward evaluation. It cannot authorise continuous paper positions or real-money trading. This holdout is consumed once when the v1.3.1 evaluator first reaches its return-calculation step; no rule may be changed afterward.
