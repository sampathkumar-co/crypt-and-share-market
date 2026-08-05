# v6.4 Sealed Dual-Source Forward Protocol

Status: frozen before the first eligible v6.3 forward prediction is generated.

## Candidate identity

The only candidate is the exact v6.3.5 dual-source consensus mechanism from draft PR #112. Its retrospective evidence status is `RETROSPECTIVE_DUAL_SOURCE_CANDIDATE_FORWARD_REQUIRED` and its authoritative report SHA-256 is `53642b99bb659fa8eabc86474ebc205742670d731f0f3f2eca6be50275459f1a`.

No parameter, member, source, cadence, exposure, cost, lag, cash treatment, or decision rule may change during this programme.

## Prospective boundary

- First eligible decision date: 2026-08-06 UTC.
- No record may be created for an earlier date.
- A prediction must be sealed before the earliest executable open whose return it will later evaluate.
- Missing dates are recorded as gaps and are never reconstructed or backfilled.
- Existing `forward-data/v2` and v3.3 evidence remain untouched and are not inputs to promotion.

## Decision semantics

- Completed UTC daily bars only.
- One completed-day signal lag.
- Binance and Coinbase source engines run independently.
- At a genuine source decision, each asset target is the minimum of the two source targets.
- Between genuine decisions, the portfolio carries natural drift with no hidden rebalance.
- BTC and ETH only.
- Long-or-cash only.
- Maximum aggregate crypto target: 10%.
- Remaining weight is cash.
- Standard round-trip cost: 20 basis points.
- Stress round-trip cost: 40 basis points.

## Sealed record

Each prediction record must contain:

- decision date and creation timestamp;
- earliest executable timestamp and evaluation horizon;
- exact candidate, protocol, implementation, and source-data fingerprints;
- independently calculated Binance and Coinbase targets;
- final dual-source target and cash weight;
- genuine-decision flag and reason;
- paper-only and authorization flags;
- previous-record hash and record self-hash.

Records are canonical JSON and append-only. Duplicate dates are rejected even when bytes match.

## Outcome separation

Prediction creation cannot read future outcome data. Outcome attachment occurs only after the horizon closes and must reference the immutable prediction hash. Standard- and stress-cost P&L are reported separately. Outcomes never modify predictions.

## Promotion gate

Promotion requires whichever occurs later:

- 365 contiguous eligible daily predictions; or
- 30 independent target-changing actions.

All of the following must also pass:

- no unresolved continuity gap;
- positive standard- and stress-cost net P&L;
- positive net P&L in both chronological halves;
- maximum drawdown no greater than 10%;
- no single action contributes more than 20% of total positive profit;
- no half contributes more than 60% of total positive profit;
- observed execution costs remain within frozen assumptions;
- source, mechanism, implementation, and protocol fingerprints remain unchanged;
- no safety violation.

Passing this programme still does not authorize live trading. A separate human-approved, tiny-capital protocol would be required.

## Safety

`paper_only=true`, `authorizes_trading=false`, and `authorizes_continuous_paper=false` are mandatory. No credentials, wallets, orders, leverage, shorts, derivatives, deposits, withdrawals, lending, or private exchange endpoints are permitted.
