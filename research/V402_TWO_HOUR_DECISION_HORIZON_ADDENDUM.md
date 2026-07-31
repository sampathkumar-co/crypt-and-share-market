# v4.0.2 Two-Hour Decision-Horizon Addendum

Status: frozen after the absolute-return composition rerun and before any two-hour-horizon result is calculated.

## Diagnosis

The corrected v4.0.1 model composed market and asset-excess predictions correctly, but all untouched and current candidates remained below the frozen 40-basis-point round-trip stress cost. The run therefore produced zero trades and zero P&L.

Costs are not reduced. The issue is economic horizon mismatch: a six-candle, 30-minute label is generally too short for a low-turnover long-or-cash system to clear conservative retail execution costs.

## Frozen correction

- Primary prediction and downside horizon becomes 24 completed five-minute candles, or two hours.
- Features remain unchanged and use only the completed signal candle and earlier data.
- Fill remains the next five-minute candle open.
- Historical decisions are sampled at non-overlapping 24-candle intervals.
- Live reasoning may refresh every completed five-minute candle, but a target may change only when:
  - the prior two-hour holding horizon has completed; or
  - the regime classifier enters panic and forces cash.
- Standard and stress costs remain 20 and 40 basis points round trip.
- Maximum exposure remains 10%, with at most 5% per asset and at least 90% cash.

## Ten-minute smoke interpretation

A ten-minute smoke run can observe quote handling, mark-to-market, prediction stability, and risk-off behaviour. It is not expected to complete a two-hour holding period and cannot establish profitability.

## Non-outcome tuning boundary

- No threshold was lowered from the zero-trade result.
- No asset, feature, cost, model family, split, or uncertainty rule changes.
- The 21-day campaign remains a discovery/smoke screen and cannot satisfy the separate 90-day verification-span gate.
- A larger fixed historical campaign is required after the implementation and live smoke path are stable.

All original paper-only and repository-isolation requirements remain unchanged.
