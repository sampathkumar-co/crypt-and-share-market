# v5.6 Cost-Aware Paper Execution Protocol

## Purpose

Correct the one-hour paper execution flaw exposed on 2026-08-02: a positive directional signal was allowed to enter even though its likely move was not safely above spread, fees, slippage and uncertainty.

## Safety boundary

- Paper-only; no exchange credentials, orders, deposits or withdrawals.
- Long-or-cash only; no leverage, shorting or averaging down.
- A literal zero-trading-loss guarantee is represented only by staying in cash.
- The loss-averse mode may still lose because markets can gap or reverse.

## Frozen cost model

- One-way fee: 10 bps.
- Estimated slippage: 2.5 bps per side.
- Minimum desired profit buffer: 10 bps.
- Maximum quoted spread: 5 bps.
- One-sided uncertainty multiplier: 1.28.
- Maximum paper allocation: 25%.
## Entry gate

For every asset, calculate:

1. Exact fee break-even in basis points.
2. Current spread plus two-sided slippage allowance.
3. A conservative confirmed trend: the smaller of 15-minute and 60-minute return.
4. A one-hour volatility penalty: `1.28 * minute_volatility * sqrt(60)`.
5. Lower-bound edge = confirmed trend minus volatility penalty.

Enter only when the lower-bound edge exceeds total execution cost plus the profit buffer, all 5/15/60-minute returns are positive, price is above 20-minute VWAP, and spread is within the cap. Otherwise hold cash.

## Exit rules

- Take profit after a positive net target is reached.
- Once a meaningful net profit exists, protect a smaller positive floor.
- Use a hard risk stop and a fixed time exit; these limit losses but cannot eliminate them.
- Never delay a required risk exit merely to avoid recording a loss.

## Acceptance checks

- The 2026-08-02 SOL entry must be rejected from its frozen pre-entry features.
- Guaranteed-no-trading-loss mode must always return cash.
- Strong synthetic signals can pass the cost-aware gate.
- Break-even, take-profit, profit-protection and hard-stop mathematics must have unit tests.
