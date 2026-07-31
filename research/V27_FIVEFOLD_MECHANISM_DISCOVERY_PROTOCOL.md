# v2.7 Fivefold Mechanism Discovery Protocol

Status: frozen before implementation or outcome access.

## Purpose

v2.5 and v2.6 showed that late residual-momentum entries do not produce a durable net edge. v2.7 therefore tests three genuinely different long-or-cash mechanisms rather than tuning the failed continuation score.

A result may be called a **historical breakthrough candidate** only if it is profitable after costs in five independent, non-overlapping validation windows and all gates below pass. It remains historical evidence and cannot replace Track A.

## Isolation and safety

- Historical research only.
- Paper-only and long-or-cash.
- No live orders, wallets, credentials, leverage, shorts, derivatives positions or execution authorization.
- Maximum one position at a time.
- Maximum asset weight: 15%.
- Minimum cash weight: 85%.
- No writes to `forward-data/v2`.
- No modification of v2.3, v2.4, v2.5, v2.6, profitability gates, selection-stability gates or Track A evidence.
- Source code, protocol, source archives and generated report are SHA-256 inventoried.

## Assets and data

Assets: BTC, ETH, SOL, AVAX, LINK and DOGE against USDT.

Public Binance archives only:

- spot 5-minute klines;
- USD-M perpetual 5-minute klines;
- funding-rate archives;
- open-interest metrics archives.

Each hourly state is assembled from exactly twelve completed 5-minute bars. A missing bar, archive, funding observation or open-interest hour excludes that hour. Missing data is never synthesized.

## Fixed windows

Each screen uses a 10-day warm-up and 28 complete UTC days. Windows are separated and fixed before outcomes.

Discovery windows:

1. `2024-07`: July 1-28, 2024.
2. `2024-12`: December 1-28, 2024.

Validation windows:

1. `2025-03`: March 1-28, 2025.
2. `2025-06`: June 1-28, 2025.
3. `2025-09`: September 1-28, 2025.
4. `2025-12`: December 1-28, 2025.
5. `2026-04`: April 1-28, 2026.

Validation windows are evaluated only after the implementation and thresholds are frozen. No threshold may change after any validation result is read.

## Timing and fills

For signal hour `t`:

- only data completed by the close of `t` may form the signal;
- hour `t+1` is a mandatory confirmation hour;
- earliest fill is the open of `t+2`;
- primary exit is the open eight hours after entry;
- four-hour and twelve-hour exits are fixed sensitivity checks;
- round-trip standard cost is 20 bps;
- round-trip stress cost is 40 bps.

Signals for a selected asset/family have a 12-hour cooldown. No new portfolio event may enter before the previous primary eight-hour event exits.

## Mechanism A: regime-conditioned pullback reclaim

This is not unconditional mean reversion. It requires:

- positive 72-hour asset trend and positive 72-hour BTC trend;
- positive beta-adjusted 72-hour residual trend;
- a controlled negative three-hour residual pullback at `t`;
- no extreme funding or open-interest crowding;
- positive spot-flow lead during the pullback or confirmation;
- confirmation at `t+1` that reclaims at least half of the pullback hour and closes with positive taker flow.

The intent is to enter a temporary pullback inside an established risk-on trend, not buy a falling market.

## Mechanism B: post-capitulation recovery

This requires:

- a large negative six-hour move or residual move;
- material six-hour open-interest contraction;
- negative or bottom-decile basis/funding pressure;
- evidence that forced selling is ending rather than continuing;
- `t+1` spot-led price recovery, basis improvement and no renewed open-interest collapse.

The intent is to enter after a liquidation flush has stabilized, never during the flush.

## Mechanism C: compression breakout hold

This requires:

- twelve-hour realized volatility and range compression relative to the previous 168 completed hours;
- non-negative 48-hour BTC regime;
- a close above the previous twelve-hour high at `t`;
- spot volume expansion and positive spot-led flow;
- limited basis/funding crowding;
- `t+1` holds above the breakout level without a large reversal.

The intent is to capture early expansion after genuine compression rather than chase an already extended move.

## Ranking and portfolio construction

- Candidate scores are monotonic combinations of preregistered mechanism evidence.
- Select at most one candidate per entry hour.
- Ties: score descending, then asset alphabetically, then family alphabetically.
- Fixed target weight: 15%.
- Cash receives the remaining 85%.
- If no candidate passes every condition, remain in cash.

## Primary acceptance gates

All gates must pass:

1. Net compounded return after 20-bps costs is positive at the eight-hour horizon.
2. Net compounded return after 40-bps costs is non-negative at the eight-hour horizon.
3. Both discovery windows are individually positive after standard costs.
4. **All five validation windows are individually positive after standard costs.**
5. At least four of five validation windows are non-negative under stress costs.
6. At least six accepted events occur in every validation window.
7. At least 45 accepted events occur across validation windows.
8. At least 70 accepted events occur across all seven windows.
9. At least two mechanism families and four assets are active in validation.
10. Portfolio maximum drawdown is at most 6%.
11. Eight-hour net return beats cash, the 15%-exposure BTC benchmark and the 15%-exposure equal-weight benchmark.
12. At least one of the four-hour or twelve-hour standard-cost sensitivity returns is positive.
13. No asset supplies more than 45% of positive validation contribution.
14. No family supplies more than 70% of positive validation contribution.
15. Every required window is complete with zero excluded hours.

## Breakthrough terminology

- `FIVEFOLD_HISTORICAL_BREAKTHROUGH_CANDIDATE`: every gate passes.
- `NOT_FIVEFOLD_VERIFIED`: one or more gates fail.

Even a passing result does not authorize trading and does not alter Track A. A forward implementation requires a new protocol committed before its first future decision.