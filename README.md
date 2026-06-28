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
```bash
PYTHONPATH=src python -m tradebot.cli scan --market crypto --folder data/crypto
PYTHONPATH=src python -m tradebot.cli scan --market equity --folder data/equity
```

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
