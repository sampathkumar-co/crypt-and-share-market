# Roadmap

## Completed in v0.2.0

- Validated CSV OHLCV loading with duplicate detection and data-quality audits.
- Read-only public crypto history with provider fallback.
- Momentum, breakout, and mean-reversion strategies.
- Next-bar single-symbol backtesting with explicit conservative intrabar fills.
- Crypto and Indian equity scanners with costs, tax estimates, liquidity, trend, and risk scoring.
- One-position crypto portfolio rotation without same-close look-ahead.
- Walk-forward parameter selection with unseen test windows and historical indicator warm-up.
- Rolling robustness and regime analysis.
- Optional dependency-light ML scoring and baseline comparison.
- Resumable paper-live simulation with no credential or order support.
- Loopback-only local dashboard with confined filesystem access.
- Console, JSON, and Markdown reports.
- Installable CLI, regression tests, and Python 3.10–3.13 GitHub Actions CI.

## Next research milestones

1. **Higher-quality datasets** — adjusted equity data, corporate actions, symbol histories, delistings, dividends, and explicit provenance metadata.
2. **Execution sensitivity** — spread models, variable slippage, partial-fill approximations, latency scenarios, and Monte Carlo order sequencing.
3. **Portfolio research** — configurable position concurrency, diversification constraints, correlation exposure, rebalancing, and benchmark portfolios.
4. **Validation depth** — purged/embargoed cross-validation, bootstrap confidence intervals, probability-of-backtest-overfitting diagnostics, and multiple-testing controls.
5. **Model governance** — drift checks, feature lineage, calibration, explainability, reproducible model cards, and challenger-versus-champion evaluation.
6. **Observability** — structured logs, run manifests, dataset hashes, deterministic seeds, report schemas, and experiment comparison tools.
7. **Usability** — richer local charts, downloadable reports, saved experiment configurations, and accessibility improvements.

## Permanent safety boundary

This repository remains a paper-research platform. It will not contain broker order placement, exchange trading endpoints, wallet access, private-key storage, leverage, withdrawals, or a hidden “live mode.”

A future real-money executor, should one ever be considered, must be a separate repository and deployment with independent security review, regulatory and tax review, broker/exchange sandbox testing, explicit human approval, immutable audit logs, hard capital limits, and an external kill switch. Paper results from this project are never sufficient approval for live trading.
