# Historical research gates

Version 0.4 introduces a fail-closed research pipeline between historical backtesting and continuous forward paper trading. Version 0.4.1 binds every authorization to the exact implementation and frozen forward configuration.

## Purpose

The gate asks whether a strategy remained positive after estimated costs and taxes across several non-overlapping unseen periods, while controlling drawdown, turnover, transaction-cost drag, and holding duration.

A passing result is permission only to begin continuous **paper** observation. It is not evidence that the strategy will remain profitable, and it is never permission to place real orders.

## What is optimized

For each of `momentum`, `breakout`, and `mean_reversion`, the engine selects strategy parameters and a patient execution profile using training data only. Execution profiles vary:

- minimum and maximum holding periods;
- cooldown after an exit;
- exit-confirmation count;
- trailing-stop distance;
- breakeven trigger;
- market-regime filtering.

The selected configuration is frozen for the following unseen period. If the independent process passes, the same validated selection process is retrained on all currently available history to produce one exact frozen forward configuration for each passing strategy.

## Independent unseen periods

Each symbol history is divided into repeating blocks:

```text
[training][unseen 1][unseen 2][unseen 3]...
```

Every unseen block is non-overlapping. For each block, the immediately preceding training window is used for selection. No unseen candle participates in that period's optimization. Warm-up candles are excluded from unseen churn and exposure denominators.

## Benchmarks

Every unseen result is compared with:

- **cash**, whose nominal return is 0%; and
- **buy-and-hold**, measured over the same unseen period.

The report records both the strategy's net return and its excess return against buy-and-hold. A strategy can pass while underperforming buy-and-hold in some periods, but it must beat buy-and-hold in the configured minimum fraction of independent periods.

## Default hard gates

A period fails when any of the following occurs:

- net return after estimated fees, slippage, and tax is not positive;
- drawdown exceeds 20%;
- transaction-cost drag exceeds 50% of gross trading activity;
- trade frequency exceeds 8 trades per 100 **tradable unseen** bars;
- a trading strategy's average holding duration is below 2 bars.

A strategy passes only when:

- at least three independent unseen periods are available;
- every unseen period has positive net return by default;
- every period passes the churn, cost, holding, and drawdown gates;
- the strategy beats buy-and-hold in at least half of the unseen periods.

These thresholds are configurable, but weakening them must be treated as a new experiment rather than as a way to rescue a failed result.

## Report identity and invalidation

Schema v1.1 records:

- a deterministic dataset fingerprint;
- the installed package version;
- a SHA-256 fingerprint of strategy, scanner, risk, tax, cost, backtest, gate, and forward-paper source files;
- the exact strategy parameters and execution settings frozen for forward paper;
- the symbols and historical range used for final retraining.

Continuous mode recomputes the implementation fingerprint at startup. A report becomes invalid immediately when evaluated behavior changes, even if its age is below the configured maximum.

## Run the gate

```bash
tradebot research-gate \
  --market crypto \
  --folder data/crypto \
  --train-size 180 \
  --test-size 60 \
  --min-periods 3 \
  --max-drawdown 0.20 \
  --max-cost-drag 0.50 \
  --max-trades-per-100 8 \
  --min-average-holding-bars 2 \
  --min-beat-buy-hold-fraction 0.50 \
  --json-out reports/research_gate.json
```

Exit status:

- `0`: at least one strategy passed all required gates;
- `2`: no strategy passed, so continuous forward paper mode remains blocked;
- other non-zero status: invalid input or execution error.

## Continuous forward paper mode

```bash
tradebot paper-live-crypto \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --interval 1m \
  --cash 100000 \
  --strategy momentum \
  --state paper_state/crypto_forward.json \
  --continuous \
  --gate-report reports/research_gate.json \
  --gate-max-age-days 90 \
  --sleep-seconds 60
```

The process refuses to start when the report is missing, stale, failed, for another market, from another implementation, does not cover every requested symbol, or has no frozen configuration for the selected strategy.

Continuous mode loads the exact frozen strategy and execution profile. Holding duration, cooldown, and exit confirmation are counted using newly observed candles rather than polling loops.

A queued signal can execute only when exactly one newer candle has appeared. If the process misses multiple candles, the signal expires instead of being backfilled at a historical price.

## Operational rule

Do not edit a gate report manually. Rerun the historical gate whenever the dataset, thresholds, package version, strategy code, cost model, execution logic, or forward simulator changes. An existing open paper position may resume only when its stored authorization exactly matches the newly validated report.
