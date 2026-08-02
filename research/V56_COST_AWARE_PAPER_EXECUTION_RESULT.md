# v5.6 Cost-Aware Paper Execution Result

## Status

`COST_AWARE_PAPER_EXECUTION_IMPLEMENTED`

This is a risk-control improvement, not a profitability breakthrough and not authorization for live trading.

## What changed

- Added an exact round-trip fee break-even calculation.
- Added spread, two-sided slippage and minimum-profit buffers.
- Penalized the confirmed 15/60-minute trend by one-hour volatility uncertainty.
- Defaulted to cash unless the lower-bound edge clears every cost.
- Limited paper allocation to 25%.
- Added take-profit, positive profit-lock, hard-stop and time-exit rules.
- Added a separate guaranteed-cash mode; this is the only mode that guarantees no trading loss.

## Frozen 2026-08-02 replay

The original paper test bought SOL and finished at `-INR 9.110018640311468` on INR 10,000.

Using the exact frozen pre-entry features:

- Required edge: 36.381675793441524 bps.
- Confirmed trend: 32.777929527449956 bps.
- Uncertainty penalty: 34.96769674487146 bps.
- Conservative lower-bound edge: -2.1897672174215046 bps.
- Corrected decision: remain cash.

## Current public-data smoke checks

- Guaranteed-cash smoke: no asset, no position, INR 0.00 P&L.
- Default loss-averse snapshot: independently rejected every asset and remained cash with INR 0.00 P&L.
- No credentials or order endpoints were used.

## Verification

- Focused tests: 16 passed.
- Full repository suite: 468 passed.
- Replay evidence SHA-256: `8f19d94fb937c143a9c18fc23077c5df01add738e48e981875f07e53f77252f8`.
- Guaranteed-cash smoke SHA-256: `1935c21785cdd09ecf1f254a9ac99543ebda6dfb9d54d589e909cd771ea9887c`.
- Loss-averse snapshot SHA-256: `154b93916a799acec0bc3e6dd2eb1cb9ac51e1c28870bf7c050c64655ec32ae5`.

## Honest limitation

Any mode that enters a volatile market can lose through reversals, gaps, latency or execution uncertainty. Stops bound intended risk but cannot promise zero realized loss. A literal zero-trading-loss requirement necessarily means remaining in cash.
