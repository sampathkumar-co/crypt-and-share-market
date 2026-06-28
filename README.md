# Dual Market AI Bot

A safe, paper-only research platform for crypto and Indian equity market strategy experiments. It scans OHLCV candles, generates BUY/HOLD/SELL signals, applies risk rules, estimates costs and Indian taxes, backtests historical data, and performs walk-forward validation.

> **Warning:** Trading is risky. This project does not guarantee profit, is not financial advice, and is not tax advice. Version 1 is paper trading only.

## What it does
- Runs crypto and Indian equity paper backtests.
- Loads and validates CSV OHLCV candle data.
- Provides momentum + volume, breakout, and mean-reversion strategies.
- Ranks symbols from CSV folders with scanner metrics.
- Applies paper risk rules before simulated trades.
- Estimates crypto/equity fees, slippage, brokerage, and simplified Indian tax impact.
- Produces console and JSON reports with net P&L, drawdown, win rate, rejected trades, and warnings.

## What it does not do
- No real wallet, exchange, or broker API connections.
- No real buy/sell orders.
- No real API key storage.
- No leverage, futures, options, or guaranteed-profit claims.
- No APK/mobile dashboard yet; the backend is prepared for later dashboard/API work.

## Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e . pytest
```
No mandatory third-party runtime dependency is required beyond Python standard library for the engine.

## Run sample crypto backtest
```bash
PYTHONPATH=src python -m tradebot.cli backtest --market crypto --symbol BTCUSDT --data data/samples/crypto_btcusdt.csv
```

## Run sample equity backtest
```bash
PYTHONPATH=src python -m tradebot.cli backtest --market equity --symbol RELIANCE --data data/samples/equity_reliance.csv
```


## Fetch public crypto historical data
```bash
PYTHONPATH=src python -m tradebot.cli fetch-crypto --symbols BTCUSDT,ETHUSDT,SOLUSDT --interval 1d --days 365 --out data/crypto
```

`fetch-crypto` uses public/read-only market data only. It does not connect wallets, place orders, store API keys, or add exchange trading APIs. Binance public klines are tried first, with a CoinGecko public-data fallback for supported symbols when Binance is inaccessible. Saved files use the project CSV format: `timestamp,open,high,low,close,volume`, so fetched data can be scanned or walk-forward tested immediately.

## Run scanner
The scanner ranks symbols by trend strength, volume strength, breakout/pullback quality, liquidity safety, volatility risk, and estimated after-cost/tax net-profit feasibility. Rejected symbols include a `rejection_reason` in console and JSON reports.

```bash
PYTHONPATH=src python -m tradebot.cli scan --market crypto --folder data/crypto --top 20 --json-out reports/crypto_scan.json
PYTHONPATH=src python -m tradebot.cli scan --market equity --folder data/equity --top 20
```


## Crypto portfolio rotation mode
```bash
PYTHONPATH=src python -m tradebot.cli portfolio-crypto --folder data/crypto --cash 100000 --top 20 --json-out reports/crypto_portfolio.json
```

Portfolio rotation mode simulates the repeated paper-only business idea: scan many crypto CSVs, pick the best accepted opportunity, enter one paper position at a time, exit on target, stop-loss, scanner-risk, or max holding period, then rotate into the next best accepted opportunity. It tracks full portfolio equity, fees, estimated taxes, rejected opportunities, hold time, and trade entry/exit reasons. It does **not** place real trades, connect wallets, use leverage/futures, or call exchange order APIs.






## Investor/demo report
```bash
PYTHONPATH=src python -m tradebot.cli demo-report --out reports/investor_demo_report.md --json-out reports/investor_demo_summary.json
```

The demo report creates an investor-friendly Markdown summary covering the project, problem statement, current modules, paper-only safety rules, latest available reports, risks, risk minimization, roadmap, product potential, and clear no-guarantee disclaimers. It is designed for honest paper-testing communication, not fundraising hype or proof of profit.

## Local dashboard/API
```bash
PYTHONPATH=src python -m tradebot.cli serve-dashboard --host 127.0.0.1 --port 8000
```
Then open `http://127.0.0.1:8000`. The local dashboard shows a big PAPER MODE ONLY warning, scanner reports, portfolio reports, robustness status, ML comparison, paper-live state, trade history, warnings, and errors. API endpoints include `GET /health`, `/reports/scanner`, `/reports/portfolio`, `/reports/robustness`, `/reports/ml-comparison`, `/paper-live/state`, `/paper-live/trades`, plus safe paper-only POST actions `/run/scan`, `/run/portfolio`, and `/run/robustness`. Requests containing `api_key`, `secret`, `wallet`, `private_key`, or `order` fields are rejected. This lightweight server is for local research only and exposes no live trading endpoint.

## Paper-live crypto simulation
```bash
PYTHONPATH=src python -m tradebot.cli paper-live-crypto --symbols BTCUSDT,ETHUSDT,SOLUSDT --interval 1m --cash 100000 --model models/crypto_signal_model.json --state paper_state/crypto_live.json --max-loops 5 --sleep-seconds 60
```

Paper-live mode is the safest live simulation step: it repeatedly fetches public/read-only candles, keeps recent in-memory history, scans current opportunities, manages one fake paper position, and writes a resumable JSON state file after every loop. It prints **PAPER MODE ONLY**, never calls exchange order endpoints, needs no API keys, uses no wallets/leverage/futures, and is still not evidence of guaranteed profit. Use `--max-loops` and `--sleep-seconds` for controlled tests.

## Crypto ML scoring layer
```bash
PYTHONPATH=src python -m tradebot.cli train-crypto-ml --folder data/crypto --model-out models/crypto_signal_model.json
PYTHONPATH=src python -m tradebot.cli evaluate-crypto-ml --folder data/crypto --model models/crypto_signal_model.json --json-out reports/crypto_ml_eval.json
PYTHONPATH=src python -m tradebot.cli scan --market crypto --folder data/crypto --top 20 --model models/crypto_signal_model.json --json-out reports/crypto_scan_ml.json
```

The ML layer is a paper-research scoring add-on. It trains a dependency-light supervised model from historical candles to estimate whether a setup may hit a target before a stop within a future holding window. Evaluation uses a chronological train/test split to reduce leakage. Accuracy, precision, recall, false-positive rate, per-symbol metrics, and low-sample warnings are reported. ML scores can support or weaken scanner opportunity scores, but they are **not proof of profit** and never trigger live trading.


## Compare portfolio rotation with and without ML
```bash
PYTHONPATH=src python -m tradebot.cli portfolio-crypto --folder data/crypto --cash 100000 --top 20 --model models/crypto_signal_model.json --json-out reports/crypto_portfolio_ml.json
PYTHONPATH=src python -m tradebot.cli compare-crypto-ml --folder data/crypto --cash 100000 --model models/crypto_signal_model.json --json-out reports/crypto_ml_comparison.json
```

`compare-crypto-ml` runs the baseline portfolio and the ML-enhanced portfolio on the same CSV folder, then reports `ML_HELPED`, `ML_NEUTRAL`, or `ML_HURT`. `ML_HELPED` requires meaningful net-return improvement without worse drawdown, weak trade count, or excessive fee/tax drag. `ML_HURT` means return, risk, overtrading, or drag became worse. Even if ML helps in paper, it does not guarantee live profit and is not permission to trade live.

## Crypto robustness testing
```bash
PYTHONPATH=src python -m tradebot.cli robustness-crypto --folder data/crypto --cash 100000 --json-out reports/crypto_robustness.json
```

Robustness mode runs the crypto portfolio rotation strategy across the full history and rolling 30/90/180-day windows where enough data exists. It classifies each window as bull/trending up, bear/trending down, sideways, or high-volatility/crash-like, then reports PASS/WATCH/FAIL. **PASS** means suitable only for continued paper testing, **WATCH** means mixed evidence, and **FAIL** means the strategy is not stable enough even for confident paper assumptions. The report highlights best/worst windows, failing regimes, profitable-window percentage, drawdown, consistency, crash survival, overtrading, low-trade, and tax-drag warnings. It never approves live trading.

## Walk-forward testing
```bash
PYTHONPATH=src python -m tradebot.cli walk-forward --market crypto --symbol BTCUSDT --data data/samples/crypto_btcusdt.csv --strategy momentum --json-out reports/walk_forward.json
```

Walk-forward testing tunes a configurable strategy parameter grid on each train window, selects the best parameters by net return, drawdown, win rate, and trade-count stability, then evaluates only those selected parameters on the unseen test window. The JSON report includes every split, all train candidates, selected parameters, train metrics, test metrics, and rejection reasons for overfitting, high drawdown, too few trades, or weak net profit after estimated costs/taxes. Supported strategy grids are `momentum`, `breakout`, and `mean_reversion`.

## Tests
```bash
PYTHONPATH=src pytest
```

## Current limitations
- CSV/mock-feed only; no real market data connector in v1.
- Tax and brokerage calculations are estimates and must be verified by a qualified professional.
- Strategies are simple baselines, not optimized AI models.
- Paper fills use candle stop/target assumptions, not order book simulation.

## Future roadmap
See [docs/ROADMAP.md](docs/ROADMAP.md).
