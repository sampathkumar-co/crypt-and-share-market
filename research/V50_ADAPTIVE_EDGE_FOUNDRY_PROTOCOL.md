# v5.0 Adaptive Edge Foundry Protocol

Status: foundation frozen before any v5 strategy outcome is calculated.

## Objective

Build a paper-only champion-challenger research platform that discovers, validates, rejects, and promotes trading hypotheses without contaminating Track A, v3.3, v4.1, or existing evidence.

Profit is never assumed. Cash is an explicit competing allocation. No component authorizes live trading.

## Non-negotiable isolation

- Existing branches and append-only evidence are read-only inputs.
- v5 development occurs on an isolated research branch.
- No historical decision, snapshot, readiness result, or experiment ledger entry may be rewritten.
- `authorizes_trading` is always false.
- Continuous paper remains unauthorized unless a later sealed protocol and ledger explicitly approve it.

## Foundation components

1. Immutable experiment specification and result fingerprints.
2. Point-in-time data manifests with source, retrieval time, availability time, and hashes.
3. Purged walk-forward validation with embargo.
4. Common cost, slippage, drawdown, turnover, and contribution accounting.
5. Champion-challenger promotion gates.
6. Append-only forward prediction sealing.
7. Fail-closed data, uncertainty, drift, and drawdown governors.

## Initial alpha hypothesis

The first candidate is a regime-conditioned, volatility-scaled, cross-sectional multi-horizon trend system with cash as a competing asset.

Learned models may rank opportunities, estimate downside, identify regimes, and quantify uncertainty. They may not bypass costs, risk limits, evidence gates, or the cash decision.

## Frozen initial safety limits

- Long-or-cash only.
- Maximum total crypto exposure: 10%.
- Maximum exposure per asset: 5%.
- Minimum cash: 90%.
- No leverage, shorts, derivatives, lending, averaging down, credentials, wallets, or exchange order endpoints.
- Standard round-trip cost: 20 basis points.
- Stress round-trip cost: 40 basis points.

## Historical candidate gate

A challenger may become a historical candidate only if all are true:

1. Untouched compounded return is positive after stress costs.
2. Maximum drawdown is no more than 10%.
3. At least four of five sequential untouched windows are positive after standard costs.
4. At least three of five are positive after stress costs.
5. At least 30 independent target-changing decisions occur.
6. No single trade contributes more than 20% of total positive profit.
7. No single window contributes more than 50% of total positive profit.
8. Results survive doubled costs and one-decision execution delay.
9. Bootstrap confidence and multiple-testing controls pass.
10. The frozen implementation reproduces on an independent source.

Passing this gate does not authorize trading or continuous paper.

## Forward promotion gate

Promotion requires at least six months of sealed, append-only forward evidence with no unresolved continuity gaps, positive stress-cost net P&L, acceptable drawdown, adequate decisions, stable calibration, broad contribution, and no safety violations.

## Multiple-testing control

Every attempted hypothesis is counted. Consumed untouched intervals remain permanently marked consumed. A candidate cannot be selected by repeatedly reopening the same test interval.

## Fail-closed conditions

Force cash or reject evaluation when data is stale, incomplete, duplicated, conflicting, unavailable at decision time, outside trained support, materially shifted, or when uncertainty, disagreement, expected costs, drawdown, or execution anomalies exceed frozen limits.

## v5 foundation milestone

The first milestone is complete only when the immutable registry, purged splitter, tests, and CI paper-only contract are merged. No profitability claim may be made from that milestone.