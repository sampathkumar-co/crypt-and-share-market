# v3.2 Execution Dependency Addendum

Status: frozen before any Coinbase replication outcome is calculated.

The original v3.2 protocol already requires a 10-day rebalance cadence and natural drift. Before Coinbase outcomes were accessed, v3.1.2 identified and corrected the inherited daily drift-rebalancing mismatch.

v3.2 must therefore use the exact scheduled-execution implementation validated by the corrected Binance audit:

- corrected Binance status: `VERIFIED_EXECUTION_CORRECTED_BINANCE_CANDIDATE`;
- corrected Binance report SHA-256: `90dea7bcc12274146f730ba5a5cd9f93179ff944211ff07de849aca68e468c22`;
- execution policy: `daily_risk_exit_entry_or_due_rebalance_only_natural_drift`;
- entries, risk-off exits and due rebalances are costed;
- unchanged trend holdings create zero turnover on non-due days;
- no model or gate changes are permitted.

This addendum changes only the implementation dependency. Coinbase prices remain unaccessed until the corrected PR passes tests and merges.
