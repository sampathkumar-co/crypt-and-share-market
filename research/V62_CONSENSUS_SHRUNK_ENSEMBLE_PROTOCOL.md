# v6.2 Parameter-Free Consensus-Shrunk Ensemble

Status: frozen after v6.1 was rejected and before any v6.2 market outcome is calculated.

## Preserved evidence

v6.1 remains rejected. Its economic pass, narrow Coinbase bootstrap failures and rank-stability failure are not rewritten or relaxed.

## Objective

Test one deterministic response to v6.1's remaining short-block downside: retain the complete fixed 16-member neighborhood, but reduce exposure automatically when members disagree.

No parameter, threshold, member or weight is selected from the exposed verification result.

## Frozen members and states

Use the exact v6.1 members, equal member importance, corrected scheduled execution, natural drift, real aggregate portfolio and no off-cadence rebalancing.

## Consensus shrink

Only when at least one member has a genuine scheduled decision or risk-off exit, calculate for each asset:

- `mean_weight = sum(member_asset_weight) / 16`
- `agreement = count(member_asset_weight > 0) / 16`
- `consensus_weight = mean_weight * agreement`

The aggregate target is the two asset consensus weights. Between genuine member decisions, the aggregate portfolio carries its natural drift unchanged.

This rule has no fitted coefficient. Full agreement reproduces the v6.1 mean target; partial agreement moves proportionally toward cash. It can never initiate more exposure than v6.1.

## Evaluation

Use the unchanged v6.1 evidence sequence and gates:

- Binance and Coinbase 2021-2025;
- standard and doubled costs;
- one-additional-day delay;
- material profitability, drawdown, action and concentration gates;
- 20/60/120-day source-specific moving-block bootstrap;
- DSR with 226 direct-lineage trials and frozen grid dispersion `0.17603369374678823`;
- the same 35-split rank-stability audit against the 16 fixed members.

The rank-stability thresholds remain unchanged: top-half in at least 80% of splits and median percentile rank at least 0.60.

## Outcomes

- `CONSENSUS_ENSEMBLE_REJECTED`
- `RETROSPECTIVE_CONSENSUS_CANDIDATE_FORWARD_REQUIRED`

A pass is still retrospective because 2021-2025 is exposed. It must be frozen into a new untouched forward programme and cannot alter v3.3 or authorize live/continuous paper trading.
