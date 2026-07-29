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
- Console, JSON, and Markdown reports.
- Installable CLI, regression tests, and Python 3.10–3.13 GitHub Actions CI.

## Completed in v0.3.0

- Production-configurable local or public dashboard server.
- Public read-only defaults and bearer-token protection for optional mutation endpoints.
- Health and storage-readiness probes.
- Configurable data, report, and state directories.
- Graceful SIGTERM shutdown and structured HTTP request logs.
- Atomic dashboard report writes.
- Non-root multi-stage Docker image with built-in health check.
- Hardened Docker Compose configuration with read-only root filesystem, dropped capabilities, and persistent state.
- Container boot smoke tests before publication.
- Multi-architecture GitHub Container Registry publishing with SBOM and provenance.
- Deployment, backup, rollback, and vulnerability-reporting documentation.
- Automated dependency update configuration.

## Completed in v0.4.0

- Strategy-aware market-regime filtering for long-only entries.
- Patient execution profiles with minimum holds, cooldowns, confirmed exits, trailing protection, and breakeven stops.
- Explicit turnover, trade-frequency, holding-duration, and transaction-cost-drag metrics.
- Cash and buy-and-hold benchmarks in single-strategy, walk-forward, portfolio, and gate reports.
- Training-only strategy and execution-profile selection.
- Non-overlapping independent unseen historical periods.
- Hard positive unseen-return, drawdown, churn, holding, and cost gates.
- Evaluation of momentum, breakout, and mean reversion under the same gate process.
- Deterministic dataset fingerprints and machine-readable gate reports.
- Continuous forward paper mode that refuses to start without a fresh passing report for the selected strategy.
- Deferred forward entries so an old historical candle cannot be treated as an immediately executable fill.

## Next research and product milestones

1. **Higher-quality datasets** — adjusted equity data, corporate actions, symbol histories, delistings, dividends, and explicit provenance metadata.
2. **Execution sensitivity** — spread models, variable slippage, partial-fill approximations, latency scenarios, and Monte Carlo order sequencing.
3. **Statistical confidence** — purged/embargoed validation, bootstrap confidence intervals, probability-of-backtest-overfitting diagnostics, and multiple-testing controls.
4. **Portfolio research** — configurable position concurrency, diversification constraints, correlation exposure, rebalancing, and benchmark portfolios.
5. **Forward-paper governance** — scheduled gate refreshes, automatic suspension on drift or drawdown, champion/challenger tracking, and immutable run manifests.
6. **Model governance** — drift checks, feature lineage, calibration, explainability, reproducible model cards, and challenger-versus-champion evaluation.
7. **Observability** — metrics export, dataset hashes, deterministic seeds, versioned report schemas, and experiment comparison tools.
8. **Storage evolution** — optional shared/object storage if multi-replica deployment becomes necessary.
9. **Usability** — richer charts, downloadable reports, saved experiment configurations, and accessibility improvements.

## Permanent safety boundary

This repository remains a paper-research platform. It will not contain broker order placement, exchange trading endpoints, wallet access, private-key storage, leverage, withdrawals, or a hidden “live mode.”

A future real-money executor, should one ever be considered, must be a separate repository and deployment with independent security review, regulatory and tax review, broker/exchange sandbox testing, explicit human approval, immutable audit logs, hard capital limits, and an external kill switch. Paper results from this project are never sufficient approval for live trading.
