# Roadmap

1. **Phase 1: Paper trading engine** — CSV validation, strategies, risk checks, costs/taxes, reports, tests, and walk-forward grid tuning with unseen test validation.
2. **Phase 2: Real market data connectors** — Read-only data feeds with no trading permissions.
3. **Phase 3: Scanner for many coins/stocks** — Larger universes, caching, scheduling, liquidity filters.
4. **Phase 4: AI/ML scoring model** — Feature engineering, model validation, drift detection, explainability.
5. **Phase 5: Dashboard/API** — Stable backend API and web dashboard for reports and monitoring.
6. **Phase 6: Mobile APK** — Mobile dashboard after backend stability and security review.
7. **Phase 7: Controlled live trading only after validation** — Tiny capital, kill switch, audit logs, read-only defaults, no withdrawals.


## Walk-forward validation upgrades

- Maintain configurable parameter grids for baseline strategies.
- Add richer train/test stability diagnostics as more sample data becomes available.
- Keep live trading blocked until paper walk-forward results are stable across multiple market regimes.
