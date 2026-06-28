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



## Crypto ML scoring layer
```bash
PYTHONPATH=src python -m tradebot.cli train-crypto-ml --folder data/crypto --model-out models/crypto_signal_model.json
PYTHONPATH=src python -m tradebot.cli evaluate-crypto-ml --folder data/crypto --model models/crypto_signal_model.json --json-out reports/crypto_ml_eval.json
PYTHONPATH=src python -m tradebot.cli scan --market crypto --folder data/crypto --top 20 --model models/crypto_signal_model.json --json-out reports/crypto_scan_ml.json
```

The ML layer is a paper-research scoring add-on. It trains a dependency-light supervised model from historical candles to estimate whether a setup may hit a target before a stop within a future holding window. Evaluation uses a chronological train/test split to reduce leakage. Accuracy, precision, recall, false-positive rate, per-symbol metrics, and low-sample warnings are reported. ML scores can support or weaken scanner opportunity scores, but they are **not proof of profit** and never trigger live trading.

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
