# v6.0 Common-Accounting Champion Tournament

Status: frozen before tournament outcomes.

## Objective

Run one paper-only, no-search comparison of the surviving project arms under a single material-profitability decision layer. This experiment does not authorize trading.

## Frozen arms

1. Yielding cash.
2. Passive BTC/ETH at the same 10% aggregate crypto risk budget.
3. Corrected v3.1.2/v3.2 trend plus yielding cash.
4. Exact v4.4 learned-model control.
5. Exact v4.4 plus the unchanged v5.2 primary trend-acceleration attenuation.

No parameter, threshold, asset, cadence, target weight, model or accounting rule may be selected from tournament outcomes.

## Integrity sequence

1. Reproduce the v3.1.2 Binance report and require its frozen SHA-256.
2. Reproduce the v3.2 Coinbase report and require its frozen SHA-256.
3. Reproduce the frozen v4.3 bundle/evaluation and v4.4 overlay before accepting v4 arms.
4. Require the frozen v5.2 primary hypothesis fingerprint and the later v5.3/v5.4.2/v5.5 evidence chain.
5. Stop closed if any dependency is missing or does not reproduce.

## Common decision layer

Every arm is normalized into a `TournamentArmEvidence` record containing:

- standard and stress window returns;
- yielding-cash window returns;
- target-changing actions;
- maximum drawdown;
- delayed-execution return;
- trade and window concentration;
- source-linked reproduction hashes;
- paper-only and authorization flags.

The layer never fabricates missing daily returns. A legacy report may be screened only on metrics it actually proves; missing required evidence is a hard failure.

## Material promotion gates

A non-cash arm is a historical breakthrough only if all are true:

- paper-only and no trading authorization;
- annualized standard net return >= 5%;
- annualized standard excess over yielding cash >= 2 percentage points;
- positive stress compounded return;
- at least four of five standard windows positive;
- at least three of five stress windows positive;
- at least 30 target-changing actions;
- maximum drawdown <= 5%;
- positive delayed-execution return;
- no single trade contributes more than 20% of positive profit;
- no single window contributes more than 50% of positive profit;
- independent-source replication is cryptographically linked;
- all frozen dependency hashes reproduce.

Deflated Sharpe, PBO and block-bootstrap gates are recorded as pending until aligned daily return series and the complete historical trial registry are present. Pending statistical gates prevent final promotion.

## Outcome vocabulary

- `DEPENDENCY_FAILED`
- `MATERIAL_GATES_FAILED`
- `STATISTICAL_GATES_PENDING`
- `HISTORICAL_BREAKTHROUGH_CANDIDATE`

Even `HISTORICAL_BREAKTHROUGH_CANDIDATE` does not authorize live or continuous paper trading. The unchanged v3.3 sealed forward programme remains the only route toward forward promotion.
