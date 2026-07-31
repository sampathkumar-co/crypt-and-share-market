# v3.3 Daily Forward Observation Protocol

Status: frozen before the first v3.3 forward observation is generated.

## Promotion basis

The exact model `sma100-rebalance10-top1-exposure10-vol2-brake20` passed the corrected five-year Binance audit and an independent Coinbase replication without retuning:

- corrected Binance report SHA-256: `90dea7bcc12274146f730ba5a5cd9f93179ff944211ff07de849aca68e468c22`;
- Coinbase replication report SHA-256: `c8a2bf7204681cdd5ce642886a42ea361f016008d908cfa16d299798cb9fefc4`;
- both reports have five positive annual portfolio returns at 20- and 40-basis-point round-trip costs;
- both reports have positive aggregate excess over identical H.15 cash;
- every active year beats cash at both cost levels;
- 2022 remains deliberately inactive and equals cash exactly;
- maximum historical drawdown is below 2% in both corrected sources.

These are historical results. They do not authorize trading or continuous shadow-paper positions.

## Frozen model

- assets: BTC and ETH only;
- price source: Coinbase Exchange public `BTC-USD` and `ETH-USD` daily candles;
- 100-day simple moving average;
- 10-day scheduled rebalance;
- select at most one asset;
- maximum target crypto exposure 10%;
- 20-day volatility target 2%;
- 20-day BTC drawdown brake 20%;
- long-or-cash only;
- daily risk-off checks;
- natural drift between scheduled rebalances;
- no daily drift rebalancing.

No parameter, source, asset or threshold search is permitted inside v3.3.

## Completed-data and latency rule

A v3.3 observation may use only Coinbase UTC daily candles whose full 24-hour interval has completed before the workflow begins.

If the latest completed candle is day `D`:

- the observation data cutoff is `D 23:59:59.999999 UTC`;
- the observation may be generated only after `D+1 00:00 UTC`;
- any new target recommendation is eligible no earlier than `D+2 00:00 UTC`.

This one-day operational latency prevents pretending that a GitHub workflow running after midnight could have filled at the already-passed `D+1 00:00` open.

## Observation actions

Each observation records exactly one action:

- `ENTER`: cash state proposes a new target at the next eligible open;
- `REBALANCE`: a due 10-day scheduled rebalance proposes a new target;
- `EXIT`: daily risk-off logic proposes cash at the next eligible open;
- `HOLD_NO_TRADE`: retain the prior selected asset with natural drift and no target rebalance;
- `CASH_NO_TRADE`: remain in cash;
- `GAP_RESET_NO_TRADE`: evidence continuity was broken; reset research state to cash and issue no target.

`HOLD_NO_TRADE` never republishes a nominal target weight because doing so would imply daily rebalancing.

## State continuity

- Observations are append-only and keyed by completed UTC candle date.
- A valid state transition requires the immediately preceding calendar-day observation.
- Missing a full observation day causes `GAP_RESET_NO_TRADE`.
- No historical observation may be backfilled after its eligible generation day.
- After a gap reset, the following contiguous day may re-enter from cash if the frozen model qualifies.
- Same-day reruns must produce byte-identical observation reports from normalized completed inputs or preserve the first valid observation.

## Sources and audit

Every observation must contain normalized SHA-256 fingerprints for:

- the completed Coinbase BTC-USD history used for features;
- the completed Coinbase ETH-USD history used for features;
- the exact Federal Reserve H.15 3-month constant-maturity observations known by the cutoff;
- the prior observation, when one exists;
- the frozen protocol, implementation and historical promotion reports.

Raw response hashes and workflow identifiers belong in a separate manifest so transport metadata cannot alter the canonical decision report.

## Exposure and authorization

- target crypto exposure may never exceed 10%;
- at most one crypto asset may be targeted;
- minimum target cash is 90%;
- `paper_only=true`;
- `authorizes_trading=false`;
- `authorizes_shadow_paper=false`;
- no credentials, wallets, orders, lending, leverage or shorts.

The output is a research observation and recommendation event, not a brokerage instruction or maintained paper portfolio.

## Append-only evidence branch

Canonical evidence is stored only on branch `forward-observation/v33`:

- `data/forward-observation-v33/observations/YYYY-MM-DD.json`;
- `data/forward-observation-v33/manifests/YYYY-MM-DD.json`.

The workflow runs daily at 00:20 UTC and again at 01:20 UTC as an idempotent recovery attempt. A shared writer lock prevents concurrent branch updates.

## Readiness boundary

No v3.3 return, P&L, drawdown, Sharpe ratio or benchmark result may be calculated until all conditions hold:

1. at least 180 contiguous canonical daily observations;
2. no unresolved gap-reset observation inside that 180-day segment;
3. at least eight target-changing actions among `ENTER`, `REBALANCE` and `EXIT`;
4. at least one qualified crypto entry;
5. all source, implementation and historical promotion fingerprints match the frozen values;
6. future Coinbase opens needed to evaluate every eligible recommendation are available.

Before readiness, status is coverage/activity only and must contain no performance fields.

## Outcome authority

Reaching readiness may unlock a separately preregistered forward evaluator. It does not automatically calculate outcomes, authorize shadow-paper operation or authorize live trading. The evaluator protocol must be committed before the first outcome is attached.
