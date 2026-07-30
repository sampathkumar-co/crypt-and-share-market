# v2.2 Forward Router Evaluation Protocol

## Status

Pre-registered before implementation and before any v2.1 decision is joined to a future return.

The protocol is intentionally stricter than a simple positive-return check. It exists to determine whether the frozen v2.1 router has a repeatable paper-research edge on genuinely forward data. It cannot authorize live trading.

## Immutable safety boundary

- Paper research only.
- No broker, exchange-order, wallet, credential, leverage or short-selling functionality.
- `authorizes_trading` remains false in every output.
- The v2.1 universe, sleeves, thresholds, ranking, risk controls and weights are frozen.
- No v2.1 rule may be changed after outcome attachment begins.
- Retired historical OHLCV datasets remain prohibited.
- Failure of this gate retires the exact v2.1 router; it does not permit weakening this protocol.

## Eligible decisions

Only canonical v2.1 decisions produced from append-only `forward-data/v2` snapshots are eligible.

A decision is eligible only when all of the following hold:

- it contains at least 169 contiguous hourly snapshot buckets;
- its protocol and implementation fingerprints match the frozen v2.1 fingerprints;
- its referenced snapshot IDs and record hashes verify against `forward-data/v2`;
- `paper_only=true` and `authorizes_trading=false`;
- the decision timestamp precedes every outcome timestamp used for that decision; and
- no later capture in the same UTC hour replaces the earliest valid capture.

Invalid, duplicated, non-contiguous or unverifiable decisions are excluded and reported. They must never be silently repaired or imputed.

## Frozen evaluation clock

The first eligible v2.1 decision defines evaluation hour zero.

The primary discovery interval opens only after at least 1,440 eligible hourly decisions have accumulated, corresponding to 60 days. The evaluator may run earlier only in readiness mode and must not calculate or disclose strategy performance.

The final 336 eligible hours, corresponding to 14 days, are an untouched chronological holdout. They must not be evaluated until the first 1,104 eligible hours have completed and the discovery result has been permanently recorded.

If fewer than 1,440 eligible hours exist, the only valid result is `INSUFFICIENT_FORWARD_HISTORY`.

## Outcome construction

For a decision at hour `t`:

- the paper entry reference is the earliest valid spot mid in hour `t+1`;
- the primary exit reference is the earliest valid spot mid in hour `t+7`, creating a frozen six-hour holding period;
- if either reference is unavailable or invalid, that asset-decision observation is excluded and reported;
- overlapping observations are allowed because the router emits independent hourly research decisions, but all uncertainty estimates must use day-block resampling rather than treating hours as independent;
- cash weight earns zero return;
- no intrabar high, low or later information is used.

The primary portfolio return is the weighted sum of selected-asset returns plus cash, after frozen round-trip costs.

## Frozen costs

The standard round-trip cost is 20 basis points per selected asset position.

The doubled-cost stress is 40 basis points per selected asset position.

Costs are charged once per eligible asset-decision observation regardless of subsequent overlapping decisions. No fee rebates, funding income, spread improvement or favorable slippage is credited.

## Benchmarks

The exact same eligible timestamps and six-hour horizons are used for all benchmarks.

1. **Cash benchmark:** 100% cash.
2. **Universe equal-weight benchmark:** equal weight across every asset with valid entry and exit references.
3. **Selected-assets passive benchmark:** equal weight across the assets selected by v2.1, ignoring router target weights and sleeve scores.
4. **BTC benchmark:** 100% BTC when BTC has valid references, otherwise cash.

All non-cash benchmarks pay the same standard and doubled round-trip costs as the router.

## Primary acceptance gate

The exact frozen v2.1 router passes discovery only if every condition below is true on the first 1,104 eligible hours:

- at least 80 active hourly decisions;
- active decisions occur on at least 20 distinct UTC days;
- at least three different assets are selected;
- standard-cost compounded return is positive;
- doubled-cost compounded return is positive;
- standard-cost mean return per active decision is positive;
- doubled-cost median return per active decision is non-negative;
- both chronological halves have positive standard-cost compounded return;
- both chronological halves have non-negative doubled-cost compounded return;
- the lower bound of a 95% day-block bootstrap confidence interval for mean daily return is greater than zero under standard costs;
- maximum peak-to-trough drawdown is no greater than 12%;
- standard-cost compounded return exceeds the cash, equal-weight universe, selected-assets passive and BTC benchmarks;
- doubled-cost compounded return exceeds the doubled-cost equal-weight universe benchmark; and
- no single UTC day contributes more than 35% of total positive strategy P&L.

A zero-activity or nearly inactive cash-preservation result cannot pass.

## Frozen robustness checks

Every robustness check uses the exact same decisions and outcomes. No thresholds are retuned.

### Leave-one-asset-out

Recompute results six times, excluding one frozen-universe asset each time.

Pass requires at least five of six reruns to have positive standard-cost compounded return and at least four of six to remain positive under doubled costs.

### Leave-one-sleeve-out

Recompute results three times, excluding all decisions attributed to one sleeve.

Pass requires at least two of three reruns to have positive standard-cost compounded return. No remaining sleeve may account for more than 80% of total positive P&L.

### Control-state robustness

Separately report decisions made under:

- full 50% exposure capacity;
- reduced 25% exposure capacity; and
- fresh versus stale/missing external-control states.

At least two populated control-state groups must have non-negative standard-cost compounded return. A populated group contains at least 20 active decisions.

### Holding-period sensitivity

Without changing entries, report three-hour and twelve-hour exits in addition to the frozen six-hour primary exit.

The six-hour result remains primary. At least one adjacent horizon must have positive standard-cost compounded return, and neither adjacent horizon may lose more than 1.5 times the absolute profit of the six-hour result.

## Final untouched holdout

The final 336 eligible hours are evaluated exactly once only if the discovery gate and every robustness gate pass.

The untouched holdout passes only if all are true:

- at least 20 active decisions on at least seven distinct UTC days;
- standard-cost compounded return is positive;
- doubled-cost compounded return is non-negative;
- return exceeds the standard-cost equal-weight universe benchmark;
- maximum drawdown is no greater than 12%; and
- no single day contributes more than 50% of positive P&L.

Failure leaves the router rejected. Passing authorizes only a separately designed, time-limited shadow-paper observation phase. It does not authorize continuous paper positions or real-money trading.

## Output contract

The evaluator must write canonical JSON containing:

- exact v2.1 decision IDs and fingerprints;
- exact snapshot IDs, record hashes and `forward-data/v2` head;
- eligibility and exclusion reasons;
- entry and exit references for every observation;
- standard and doubled costs;
- primary and benchmark return series;
- activity, concentration, drawdown and chronological-half diagnostics;
- day-block bootstrap method, seed and confidence interval;
- every robustness result;
- discovery and holdout boundaries;
- explicit pass/fail reasons; and
- `paper_only=true`, `authorizes_trading=false`.

## Implementation sequencing

1. Commit this protocol alone.
2. Implement a readiness-only evaluator that verifies inputs and reports whether 1,440 eligible hours exist without calculating returns early.
3. Freeze evaluator tests and implementation fingerprint.
4. Attach outcomes only after the minimum history exists.
5. Permanently record the discovery result before unlocking the final 336-hour holdout.
