# v5.2 Adversarial Alpha Funnel Implementation Contract

## Determinism

- Seed: 5202026.
- Raw hypothesis count: exactly 100,000.
- Sampling is stratified by mechanism family.
- Canonical JSON determines specification identity.
- Packed intervention behavior determines behavioral identity.

## Inputs

Use only frozen v4.4/v4.3 research inputs already available in the repository:
spot and perpetual bars, funding, open interest, basis, flow, cross-asset market
features, baseline model predictions and prior-known cash rates.

Every feature value must exist by the decision candle close. Every simulated
fill remains the next bar open.

## Date-level source panel

The implementation may expose these source classes:

- duplicated market-state features read once per date;
- cross-sectional mean, median, standard deviation, range and positive breadth;
- baseline regime probabilities and their disagreement;
- selected-candidate return, downside, rank, utility and disagreement estimates;
- candidate count and top-versus-second opportunity gaps.

Source names, aggregations and feature indexes must be written to the report.
No label, future return, future regime or sealed evaluation value may enter the
source panel.

## Cheap proxy

For each fold, derive the frozen baseline daily return and prior-known cash
return. Approximate risky return as baseline daily return minus cash return.
Candidate target multipliers persist from each frozen rebalance decision until
the next decision. Proxy excess is the resulting reduction or retention of
risky return, less a conservative intervention penalty.

The proxy is only a computational funnel. Exact simulation decides survival.

## Behavioral deduplication

Pack candidate activity on eligible rebalance dates across every exposed fold.
Hash the packed bytes plus multiplier. Identical hashes retain the lower
complexity rule, then lexicographically smaller canonical specification.

## Stage limits

- Retain at most 4,096 proxy candidates after deduplication.
- Run full adversarial proxy attacks on at most 512 candidates.
- Run exact standard simulations on at most 64 candidates.
- Run exact doubled-cost and neighbor simulations on at most 16 candidates.
- Freeze at most three behaviorally distinct mechanism families.

These are maxima, not targets. Earlier rejection is preferred.

## Exact simulation adapter

Map inactive dates to probability 1.0 and active dates to probability 0.0,
then reuse the validated v4.8 attenuation simulator with threshold 0.5 and the
candidate multiplier. Safety invariants must remain true in every simulation.

## Required report fields

The JSON report must include raw and unique counts, rejection counts by stage,
family coverage, source inventory, shortlist details, every attack result,
exact fold results, safety checks, runtime versions, source provenance and the
hashes of this contract, the protocol and the implementation.

The report must explicitly state:

- `paper_only: true`;
- `authorizes_trading: false`;
- `retrospective: true`;
- `untouched_historical_dates: false`;
- `sealed_evaluation_performed: false`.

An empty shortlist is a valid and preferred outcome when robustness fails.
