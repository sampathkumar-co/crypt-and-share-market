# v4.0.4 Six-Hour Primary-Horizon Addendum

Status: frozen after the v4.0.3 zero-activity result and before any six-hour model outcome is calculated.

## Horizon diagnostic

Using the same frozen completed-candle dataset, without fitting or evaluating a six-hour strategy:

- two-hour observations exceeded 40 basis points approximately 19.4% of the time;
- six-hour observations exceeded 40 basis points approximately 29.3% of the time;
- twelve-hour observations exceeded 40 basis points approximately 36.6% of the time, but also breached the frozen downside threshold approximately 40.7% of the time.

The six-hour horizon offers a better cost-clearing opportunity rate without adopting the substantially higher twelve-hour downside base rate.

## Frozen correction

- Primary prediction and downside horizon becomes 72 completed five-minute candles, or six hours.
- Fill remains the next five-minute candle open.
- Historical decisions are sampled at non-overlapping 72-candle intervals.
- Live reasoning may refresh every completed five-minute candle.
- A target may change only after the six-hour horizon completes or when panic forces cash.
- Return, market, upside, downside, regime, and uncertainty model families remain unchanged.
- The same training/calibration/test chronology and fixed hyperparameter grid remain unchanged.
- Opportunity thresholds remain the fixed calibration set 0.25, 0.35, and 0.45.
- Standard and stress costs remain 20 and 40 basis points round trip.
- Total exposure remains at most 10%, at most 5% per asset.

## Ten-minute smoke boundary

A ten-minute current-market run can test data, reasoning stability, target creation, mark-to-market, and ledger safety. It cannot complete the six-hour holding horizon or establish profitability.

## Non-outcome tuning boundary

- No costs, downside limits, uncertainty limits, or exposure limits are relaxed.
- The horizon is selected from pre-fit opportunity/downside base-rate diagnostics, not strategy P&L.
- The 21-day screen still cannot satisfy the separate 90-day verification-span gate.

All original paper-only and repository-isolation requirements remain unchanged.
