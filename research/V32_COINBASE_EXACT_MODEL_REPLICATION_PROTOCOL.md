# v3.2 Coinbase Exact-Model Replication Protocol

Status: frozen before any Coinbase replication outcome is calculated.

## Question

Does the exact v3.1 selected BTC/ETH trend overlay reproduce on an independent spot-price source when the model, cash series, years, fills and costs are unchanged?

This is a replication, not a new model-selection exercise.

## Frozen model

The only permitted model is the v3.1 selected model:

- model ID: `sma100-rebalance10-top1-exposure10-vol2-brake20`;
- 100-day simple moving average;
- rebalance every 10 days;
- select at most one asset;
- maximum crypto exposure 10%;
- 20-day volatility target 2%;
- 20-day BTC drawdown brake 20%;
- assets: BTC and ETH only;
- long-or-cash only.

No model grid, threshold search, asset search or post-outcome alteration is permitted.

## Independent price source

- Provider: Coinbase Exchange public REST API.
- Products: `BTC-USD` and `ETH-USD`.
- Endpoint: `/products/{product_id}/candles`.
- Granularity: 86,400 seconds, one day.
- Each request covers no more than 250 calendar days, below Coinbase's documented 300-candle maximum.
- Returned candles are filtered to the declared UTC interval, deduplicated and required to be complete.
- Every raw response URL and SHA-256 is recorded.

## Frozen interval and chronology

- Price warm-up begins 14 June 2020 UTC.
- Verification years are independently reset and scored:
  - 2021;
  - 2022;
  - 2023;
  - 2024;
  - 2025.
- A final 1 January 2026 open is required for the 31 December 2025 return.
- Signals use completed prior-day candles.
- Positions fill at the next UTC daily open.
- Natural drift is preserved between scheduled rebalances.
- Every year starts and ends in cash and pays terminal liquidation cost.

## Cash and costs

- Cash uses the exact v3.1.1 Federal Reserve H.15 3-month constant-maturity series and prior-day accrual chronology.
- Standard round-trip cost assumption: 20 basis points.
- Stress round-trip cost assumption: 40 basis points.
- The strategy and pure-cash benchmark receive identical cash returns.

## Frozen replication gate

The result is `VERIFIED_FIVE_YEAR_COINBASE_REPLICATION` only if all conditions hold:

1. all required Coinbase and H.15 inputs are complete and hash-audited;
2. all five annual portfolio returns are strictly positive at standard costs;
3. all five annual portfolio returns are strictly positive at stress costs;
4. aggregate excess return over cash is strictly positive at both cost levels;
5. at least four years contain crypto actions;
6. every active year has strictly positive excess return over cash at both cost levels;
7. every inactive year matches the cash benchmark within `1e-12` and contains no hidden turnover;
8. total crypto action days are at least 20;
9. both BTC and ETH are selected somewhere across the five years;
10. maximum drawdown is no more than 5%;
11. neither one asset nor one year supplies more than 80% of positive excess contribution.

A deliberately inactive bear-market year is not treated as a failed trade year, but it cannot contribute alpha or satisfy the active-year count.

## Outcome authority

- Pull requests run tests only and may not download or calculate verification outcomes.
- The first post-merge workflow run is authoritative.
- The deterministic report is stored only on `historical-results/v32` with an immutable run copy and `latest.json`.
- A successful replication can justify a separate forward-observation protocol; it cannot authorize live trading or continuous paper positions by itself.

## Safety boundary

- paper-only;
- long-or-cash;
- maximum 10% crypto exposure;
- no credentials, wallets, orders, lending, leverage or shorts;
- no modification of Track A, v3.1, v3.1.1, their evidence or their gates;
- `authorizes_trading=false`;
- `authorizes_shadow_paper=false`;
- historical replication cannot replace forward evidence.
