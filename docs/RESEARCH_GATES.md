# Historical research gates

Version 0.4 introduces a fail-closed research pipeline between historical backtesting and continuous forward paper trading.

## Purpose

The gate is designed to answer a narrower question than “did one backtest make money?” It asks whether a strategy remained positive after estimated costs and taxes across several non-overlapping unseen periods, while controlling drawdown, turnover, transaction-cost drag, and holding duration.

A passing result is permission only to begin continuous **paper** observation. It is not evidence that the strategy will remain profitable, and it is never permission to place real orders.

## What is optimized

For each of `momentum`, `breakout`, and `mean_reversion`, the engine selects strategy parameters and a patient execution profile using training data only. Execution profiles vary:

- minimum and maximum holding periods;
- cooldown after an exit;
- exit-confirmation count;
- trailing-stop distance;
- breakeven trigger;
- market-regime filtering.

The selected configuration is then frozen for the following unseen period.

## Independent unseen periods

Each symbol history is divided into repeating blocks:

```text
[training][unseen 1][unseen 2][unseen 3]...
```

Every unseen block is non-overlapping. For each block, the immediately preceding training window is used for selection. No unseen candle participates in that period's optimization.

## Benchmarks

Every unseen result is compared with:

- **cash**, whose nominal return is 0%; and
- **buy-and-hold**, measured over the same unseen period.

The report records both the strategy's net return and its excess return against buy-and-hold. A strategy can pass while underperforming buy-and-hold in some periods, but it must beat buy-and-hold in the configured minimum fraction of independent periods.

## Default hard gates

A period fails when any of the following occurs:

- net return after estimated fees, slippage, and tax is not positive;
- drawdown exceeds 20%;
- transaction-cost drag exceeds 50% of absolute gross trading P&L;
- trade frequency exceeds 8 trades per 100 bars;
- a trading strategy's average holding duration is below 2 bars.

A strategy passes only when:

- at least three independent unseen periods are available;
- every unseen period has positive net return by default;
- every period passes the churn, cost, holding, and drawdown gates;
- the strategy beats buy-and-hold in at least half of the unseen periods.

These thresholds are configurable, but weakening them must be treated as a new experiment rather than as a way to rescue a failed result.

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

The JSON report includes a deterministic dataset fingerprint, all per-period training selections, unseen metrics, rejection reasons, passing strategies, and the selected champion.

## Continuous forward paper mode

Continuous mode requires a fresh passing gate report for the selected strategy:

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

The process refuses to start when the report is missing, stale, failed, for another market, or does not list the selected strategy as passing.

Continuous mode also defers a new signal until a newly observed candle exists, preventing a historical candle from being treated as an executable forward fill.

## Operational rule

Do not edit a gate report manually. Rerun the historical gate whenever the dataset, thresholds, strategy code, cost model, or execution logic changes. The report authorizes only the exact paper-research stage represented by its generated data and code version.
