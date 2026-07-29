# Dual Market AI Bot

A dependency-light, **paper-only** research platform for testing crypto and Indian equity strategies. It provides validated OHLCV pipelines, realistic next-bar backtesting, scanning, patient portfolio rotation, walk-forward optimization, independent historical gates, forward paper simulation, and a deployable dashboard.

> **Safety boundary:** this project never places real orders, stores exchange or broker credentials, connects wallets, uses leverage, or guarantees profit. It is research software—not financial or tax advice.

## Current status

Version **0.4.0** adds a fail-closed validation stage before continuous forward paper trading.

- Signals use completed candles and entries execute at the next available open.
- The default fill policy is conservative when stop and target occur in one candle.
- Regime filters keep long-only strategies out of unsuitable bear or high-risk conditions.
- Minimum holding periods, cooldowns, confirmed exits, trailing protection, and turnover ceilings reduce frequent rotation.
- Every result reports fees, slippage, estimated taxes, cost drag, trade frequency, holding duration, cash return, and buy-and-hold return.
- Strategy parameters and execution settings are selected on training data only.
- Selected configurations must produce positive net returns across several independent unseen periods.
- Continuous forward paper mode requires a fresh passing gate report for the chosen strategy.
- The Docker deployment remains public read-only by default.

A historical gate PASS permits only continuous **paper** observation. It does not approve real-money trading.

## Features

- **Data validation** — OHLCV consistency, duplicate rejection, interval/gap estimates, zero-volume counts, and date-range summaries.
- **Public crypto history** — read-only Binance klines with a CoinGecko fallback for supported symbols.
- **Strategies** — momentum + volume, breakout, and mean reversion.
- **Patient execution** — minimum/maximum holds, cooldowns, exit confirmation, trailing stops, breakeven protection, and market-regime filtering.
- **Benchmarked backtests** — cash and buy-and-hold comparisons, drawdown, Sharpe, Sortino, Calmar, profit factor, expectancy, exposure, turnover, and cost drag.
- **Crypto and equity scanners** — opportunity, liquidity, trend, volatility, expected move, and after-cost/tax ranking.
- **Crypto portfolio rotation** — one paper position at a time with explicit turnover limits rather than automatic rapid rotation.
- **Walk-forward validation** — training-only selection followed by cost-aware unseen evaluation.
- **Historical research gate** — all three strategies evaluated across independent periods with hard positive-return, drawdown, churn, holding, and cost gates.
- **Continuous forward paper mode** — blocked unless a fresh historical report approves the exact selected strategy.
- **Optional ML scoring** — dependency-light supervised scoring and baseline comparison.
- **Dashboard/API** — local research actions or public read-only mode with protected optional mutations.
- **Deployment packaging** — non-root Docker, hardened Compose, persistent storage, graceful shutdown, health probes, GHCR publishing, SBOM, and provenance.

## Installation

```bash
python -m venv .venv
```

Linux/macOS:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The installed command is `tradebot`. The engine has no mandatory third-party runtime dependency.

## Validate market data

```bash
tradebot validate-data \
  --data data/samples/crypto_btcusdt.csv \
  --json-out reports/crypto_data_audit.json
```

CSV columns:

```text
timestamp,open,high,low,close,volume
```

## Backtest with patient execution

```bash
tradebot backtest \
  --market crypto \
  --symbol BTCUSDT \
  --data data/samples/crypto_btcusdt.csv \
  --strategy momentum \
  --cash 100000 \
  --intrabar-policy worst_case \
  --regime-filter \
  --min-holding-bars 3 \
  --max-holding-bars 40 \
  --cooldown-bars 2 \
  --exit-confirmation-bars 2 \
  --json-out reports/btc_momentum.json
```

Supported strategies are `momentum`, `breakout`, and `mean_reversion`. `best_case` intrabar ordering is available only for sensitivity analysis and should not be treated as the primary result.

## Fetch public crypto history

```bash
tradebot fetch-crypto \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --interval 1d \
  --days 730 \
  --out data/crypto
```

Larger historical gates generally need substantially more data than the bundled demonstration files.

## Scan symbols

```bash
tradebot scan --market crypto --folder data/crypto --top 20 --json-out reports/crypto_scan.json
tradebot scan --market equity --folder data/equity --top 20 --json-out reports/equity_scan.json
```

## Run patient crypto portfolio rotation

```bash
tradebot portfolio-crypto \
  --folder data/crypto \
  --cash 100000 \
  --top 20 \
  --min-holding-bars 3 \
  --max-holding-bars 30 \
  --cooldown-bars 2 \
  --max-trades-per-100 8 \
  --json-out reports/crypto_portfolio.json
```

Entries are ranked using only candles before the execution timestamp. The portfolio stays in cash when the market regime is unsuitable, the cooldown is active, the turnover ceiling is reached, or the estimated after-cost/tax edge is too small.

## Walk-forward validation

```bash
tradebot walk-forward \
  --market crypto \
  --symbol BTCUSDT \
  --data data/crypto/BTCUSDT.csv \
  --strategy momentum \
  --train-size 180 \
  --test-size 60 \
  --regime-filter \
  --json-out reports/walk_forward.json
```

By default, a selected configuration must have positive net return after estimated costs in every unseen window and remain below the configured churn, cost-drag, and drawdown limits.

## Historical research gate

Run this before continuous forward paper trading:

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

The command evaluates momentum, breakout, and mean reversion. It returns exit code `0` only when at least one strategy passes every required gate. It returns `2` when no strategy qualifies, leaving continuous paper mode blocked.

Each report contains:

- deterministic dataset fingerprint;
- selected training parameters and execution profile;
- independent unseen-period metrics;
- cash and buy-and-hold benchmarks;
- turnover, trades per 100 bars, average holding duration, and cost drag;
- per-period rejection reasons;
- passing strategies and the selected champion.

See [Historical Research Gates](docs/RESEARCH_GATES.md) for the complete methodology.

## Continuous forward paper trading

A bounded paper test can still be run directly:

```bash
tradebot paper-live-crypto \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --interval 1m \
  --cash 100000 \
  --strategy momentum \
  --state paper_state/crypto_test.json \
  --max-loops 5 \
  --sleep-seconds 60
```

Continuous mode requires a fresh passing gate report that lists the selected strategy:

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

The process refuses to start if the report is absent, failed, stale, for another market, or does not approve the chosen strategy. Continuous mode queues signals until a newly observed candle exists instead of treating an old historical candle as a forward fill.

## Robustness and optional ML

```bash
tradebot robustness-crypto \
  --folder data/crypto \
  --cash 100000 \
  --json-out reports/crypto_robustness.json

tradebot train-crypto-ml \
  --folder data/crypto \
  --model-out models/crypto_signal_model.json

tradebot compare-crypto-ml \
  --folder data/crypto \
  --cash 100000 \
  --model models/crypto_signal_model.json \
  --json-out reports/crypto_ml_comparison.json
```

ML remains an optional scoring experiment. A model result never bypasses the historical gate.

## Dashboard modes

Local mode:

```bash
tradebot serve-dashboard --host 127.0.0.1 --port 8000
```

Public read-only mode:

```bash
tradebot serve-dashboard --host 0.0.0.0 --port 8000 --public
```

Main endpoints include `GET /health`, `GET /ready`, report/state GET endpoints, and optional protected POST research actions. Public mutation endpoints require explicit enablement and a bearer token of at least 32 characters.

## Deploy with Docker Compose

```bash
cp .env.example .env
mkdir -p runtime
docker compose up --build -d
curl http://127.0.0.1:8000/ready
```

The deployment runs as a non-root user with a read-only root filesystem, all Linux capabilities dropped, `no-new-privileges`, read-only market data, persistent reports/state, and public read-only application mode.

Use immutable GHCR image tags in production:

```text
ghcr.io/sampathkumar-co/crypt-and-share-market:sha-<commit-sha>
```

Put an HTTPS reverse proxy or managed TLS ingress in front of the private application port. See the full [Deployment Runbook](docs/DEPLOYMENT.md).

## Tests

```bash
python -m pytest
```

CI installs the package, compiles the source, and runs the full suite on Python 3.10–3.13. The container workflow additionally builds and boots the real image, verifies health/readiness, confirms public mutations are disabled, and checks non-root execution.

## Important limitations

- OHLCV candles cannot reproduce order-book depth, queue position, latency, partial fills, exchange outages, circuit limits, or market impact.
- Same-candle stop/target ordering is unknowable without finer-grained data.
- Public datasets may contain gaps, revisions, symbol mapping differences, or survivorship bias.
- Cash is represented as a 0% nominal benchmark; interest and inflation are not modelled.
- Buy-and-hold comparisons do not fully model dividends, corporate actions, delistings, or rebalancing.
- Tax and fee values are configurable estimates and are not individual tax advice.
- Gate thresholds can reject every strategy. That is a valid result, not a software failure.
- A historical gate PASS does not guarantee forward paper success.
- No live trading code is included or approved.

## Project structure

```text
Dockerfile       non-root production image
compose.yaml     hardened single-instance deployment

src/tradebot/
  api/           local/public paper-research dashboard
  backtest/      backtests, regime filter, research gate, portfolio, forward paper
  data/          CSV validation and public crypto history
  ml/            optional scoring model
  reports/       console, JSON, and demo reporting
  risk/          sizing, fees, slippage, and tax estimates
  scanner/       crypto and equity ranking
  strategies/    momentum, breakout, and mean reversion

tests/           regression, integrity, gate, API, and deployment tests
docs/            research gates, deployment, risk policy, and roadmap
```

See [Research Gates](docs/RESEARCH_GATES.md), [Deployment](docs/DEPLOYMENT.md), [Security](SECURITY.md), [Risk Policy](docs/RISK_POLICY.md), and [Roadmap](docs/ROADMAP.md).
