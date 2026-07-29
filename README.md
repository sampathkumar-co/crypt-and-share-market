# Dual Market AI Bot

A dependency-light, **paper-only** research platform for testing crypto and Indian equity strategies. It provides validated OHLCV data pipelines, next-bar backtesting, multi-symbol scanning, crypto portfolio rotation, walk-forward validation, robustness analysis, optional ML scoring, paper-live simulation, and a deployable dashboard.

> **Safety boundary:** this project never places real orders, stores exchange or broker credentials, connects wallets, uses leverage, or guarantees profit. It is research software—not financial or tax advice.

## Current status

Version **0.3.0** is deployment-ready as a paper-research service.

- Python package and `tradebot` CLI.
- Automated tests on Python 3.10–3.13.
- Non-root Docker image with health and readiness probes.
- Hardened Docker Compose deployment.
- GitHub Actions container build, boot smoke test, multi-architecture publishing, SBOM, and provenance.
- Public dashboard mode that is read-only by default.
- Optional protected research actions requiring an explicit enable flag and strong bearer token.
- Persistent report and paper-state directories with a deployment and rollback runbook.

Key research-integrity rules:

- Signals are generated only from completed candles.
- Backtest and portfolio entries execute at the next available candle open.
- Gap exits are modelled explicitly.
- Candles that hit both stop and target use an explicit intrabar policy; the default is conservative `worst_case`.
- Fees, slippage, simplified taxes, and VDA TDS cash-flow estimates are reported separately.
- Results are compared with buy-and-hold and include drawdown, Sharpe, Sortino, Calmar, profit factor, expectancy, and exposure.

## Features

- **CSV validation and audits** — OHLCV checks, duplicate timestamp rejection, interval/gap estimates, zero-volume counts, and date-range summaries.
- **Public crypto history** — read-only Binance klines with a CoinGecko fallback for supported symbols.
- **Strategies** — momentum + volume, breakout, and mean reversion.
- **Realistic paper backtests** — next-open execution, configurable intrabar ordering, costs, tax estimates, benchmark comparison, and JSON reports.
- **Crypto and equity scanners** — opportunity, liquidity, trend, volatility, expected-move, and after-cost/tax ranking.
- **Crypto portfolio rotation** — one paper position at a time across multiple symbols with stops, targets, scanner-risk exits, and maximum holding periods.
- **Walk-forward validation** — parameter selection on training windows and evaluation only on unseen windows, with historical warm-up but no training-period trades.
- **Robustness analysis** — rolling-window and market-regime checks with PASS/WATCH/FAIL research classifications.
- **Optional ML scoring** — dependency-light supervised scoring, chronological evaluation, and baseline-versus-ML comparison.
- **Paper-live mode** — resumable fake-position simulation using public market data only.
- **Dashboard/API** — local mode with research actions, or public read-only mode with protected optional mutations.
- **Deployment packaging** — Docker, Compose, persistent volumes, graceful shutdown, security headers, health probes, and GHCR publishing.

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

Install the project and development tools:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The installed command is `tradebot`. The engine itself uses only the Python standard library.

## Validate market data

```bash
tradebot validate-data \
  --data data/samples/crypto_btcusdt.csv \
  --json-out reports/crypto_data_audit.json
```

CSV columns must be:

```text
timestamp,open,high,low,close,volume
```

## Backtest a strategy

```bash
tradebot backtest \
  --market crypto \
  --symbol BTCUSDT \
  --data data/samples/crypto_btcusdt.csv \
  --strategy momentum \
  --cash 100000 \
  --intrabar-policy worst_case \
  --json-out reports/btc_momentum.json
```

Equity example:

```bash
tradebot backtest \
  --market equity \
  --symbol RELIANCE \
  --data data/samples/equity_reliance.csv \
  --strategy breakout \
  --json-out reports/reliance_breakout.json
```

Supported strategies are `momentum`, `breakout`, and `mean_reversion`. `best_case` intrabar ordering exists for sensitivity analysis, but should not be treated as the primary result.

## Fetch public crypto history

```bash
tradebot fetch-crypto \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --interval 1d \
  --days 365 \
  --out data/crypto
```

This command uses public/read-only market-data endpoints. It has no exchange-order or wallet integration.

## Scan symbols

```bash
tradebot scan --market crypto --folder data/crypto --top 20 --json-out reports/crypto_scan.json
tradebot scan --market equity --folder data/equity --top 20 --json-out reports/equity_scan.json
```

## Run crypto portfolio rotation

```bash
tradebot portfolio-crypto \
  --folder data/crypto \
  --cash 100000 \
  --top 20 \
  --json-out reports/crypto_portfolio.json
```

The scanner evaluates only candles before an execution timestamp; an accepted candidate enters at the current candle open. This prevents the same closing price from being used to both discover and fill a trade.

## Walk-forward validation

```bash
tradebot walk-forward \
  --market crypto \
  --symbol BTCUSDT \
  --data data/samples/crypto_btcusdt.csv \
  --strategy momentum \
  --train-size 30 \
  --test-size 15 \
  --json-out reports/walk_forward.json
```

Each split records all training candidates, selected parameters, training metrics, unseen test metrics, acceptance status, and rejection reasons.

## Robustness testing

```bash
tradebot robustness-crypto \
  --folder data/crypto \
  --cash 100000 \
  --json-out reports/crypto_robustness.json
```

A PASS means only that the configuration is suitable for more paper testing. It is never permission to trade real money.

## Optional ML research layer

```bash
tradebot train-crypto-ml \
  --folder data/crypto \
  --model-out models/crypto_signal_model.json

tradebot evaluate-crypto-ml \
  --folder data/crypto \
  --model models/crypto_signal_model.json \
  --json-out reports/crypto_ml_eval.json

tradebot compare-crypto-ml \
  --folder data/crypto \
  --cash 100000 \
  --model models/crypto_signal_model.json \
  --json-out reports/crypto_ml_comparison.json
```

The ML component is a scoring experiment, not an autonomous profit engine. Chronological evaluation reduces leakage but does not remove regime shift, selection bias, or live-execution risk.

## Paper-live simulation

```bash
tradebot paper-live-crypto \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --interval 1m \
  --cash 100000 \
  --state paper_state/crypto_live.json \
  --max-loops 5 \
  --sleep-seconds 60
```

This mode manages fake positions and saves resumable state. It needs no API key and cannot place orders.

## Dashboard modes

Local mode keeps the original convenient research actions:

```bash
tradebot serve-dashboard --host 127.0.0.1 --port 8000
```

Public mode must be explicitly enabled and is read-only by default:

```bash
tradebot serve-dashboard --host 0.0.0.0 --port 8000 --public
```

Main endpoints:

- `GET /health` — liveness and mode information.
- `GET /ready` — data readability and state/report storage readiness.
- report and paper-live GET endpoints.
- `POST /run/scan`, `/run/portfolio`, and `/run/robustness` only when mutations are enabled.

For protected public research actions, set `TRADEBOT_ENABLE_MUTATIONS=true`, provide a `TRADEBOT_ADMIN_TOKEN` of at least 32 characters, and send it as `Authorization: Bearer ...`.

## Deploy with Docker Compose

```bash
cp .env.example .env
mkdir -p runtime
docker compose up --build -d
curl http://127.0.0.1:8000/ready
```

The Compose configuration:

- binds only to host loopback by default;
- runs with a read-only root filesystem;
- drops all Linux capabilities;
- enables `no-new-privileges`;
- mounts market data read-only;
- persists reports and state under `runtime/`;
- starts in public read-only mode inside the container.

After the container workflow runs on `main`, immutable images are published as:

```text
ghcr.io/sampathkumar-co/crypt-and-share-market:sha-<commit-sha>
```

Use the immutable SHA tag in production. Put an HTTPS reverse proxy or managed TLS ingress in front of the private application port.

See the full [deployment runbook](docs/DEPLOYMENT.md) for image usage, storage, tokens, health probes, backups, updates, and rollback.

## Investor/demo report

```bash
tradebot demo-report \
  --out reports/investor_demo_report.md \
  --json-out reports/investor_demo_summary.json
```

This creates an honest paper-research summary, not proof of profitability or a fundraising claim.

## Tests

```bash
python -m pytest
```

The suite covers data validation, scanner logic, risk/cost/tax calculations, realistic execution, portfolio rotation, walk-forward selection, robustness, ML training/evaluation, paper-live state, dashboard behavior, deployment authentication, path confinement, and JSON reports.

The Container workflow additionally builds the real Docker image, boots it, verifies `/health` and `/ready`, confirms mutation endpoints are disabled, and checks that it runs as the non-root `tradebot` user before publishing.

## Tax and cost modelling

Defaults are configurable estimates intended for comparative research. The engine models simplified Indian listed-equity STCG/LTCG rates and VDA gain tax, while reporting VDA TDS as a separate cash-flow estimate based on transfer consideration. It does not model annual exemptions, slab interactions, surcharge, residency, loss set-off, total turnover, or an individual's filing position. Verify every tax assumption with a qualified professional before relying on it.

## Important limitations

- Candle data cannot reproduce order-book depth, queue position, latency, partial fills, exchange outages, circuit limits, or market impact.
- Same-candle stop/target order is unknowable from OHLCV alone; the default deliberately chooses the worse outcome.
- Public data sources can have gaps, revisions, rate limits, symbol mapping differences, or survivorship bias.
- Sample datasets are demonstrations, not evidence of performance.
- Strategies and ML models can overfit and can fail after market regimes change.
- Equity corporate actions, dividends, symbol changes, and delistings are not fully modelled.
- Filesystem-backed reports and state currently require a single application replica.
- No live trading code is included or approved.

## Project structure

```text
Dockerfile       non-root production image
compose.yaml     hardened single-instance deployment

src/tradebot/
  api/           local/public paper-research dashboard
  backtest/      single-symbol, portfolio, walk-forward, robustness, paper-live
  data/          CSV validation and public crypto history
  ml/            optional dependency-light scoring model
  reports/       console, JSON, and demo reporting
  risk/          sizing, fees, slippage, and tax estimates
  scanner/       crypto and equity ranking
  strategies/    momentum, breakout, and mean reversion

tests/           regression, integrity, API, and deployment tests
docs/            deployment, risk policy, and roadmap
```

See [Deployment](docs/DEPLOYMENT.md), [Security](SECURITY.md), [Risk Policy](docs/RISK_POLICY.md), and [Roadmap](docs/ROADMAP.md).
