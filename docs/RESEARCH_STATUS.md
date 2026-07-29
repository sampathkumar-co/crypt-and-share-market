# Research Approval Status

The deployable application is a **paper-only research service**. Strategy profitability is not assumed from a successful build or workflow.

## Current decision

`research/experiment_ledger.json` is the machine-readable source of truth. The current ledger records all completed profitability experiments, contains no approved strategy, and sets both `continuous_paper_authorized` and `live_trading_authorized` to `false`.

The dashboard exposes the normalized, fail-closed view at:

```text
GET /research/status
```

`GET /health` also includes the deployment mode, approved-strategy count, and continuous-paper authorization flag.

## Continuous paper authorization

Continuous paper mode now requires two independent approvals:

1. A fresh passing historical research-gate report with the exact implementation fingerprint and frozen configuration.
2. A valid research ledger that explicitly lists the requested strategy and authorizes continuous paper.

The ledger fingerprint is stored with the paper-state gate authorization. An existing position or pending entry cannot continue after either the gate configuration or the approval ledger changes.

Missing, malformed, inconsistent, or live-trading-authorizing ledgers fail closed. One-shot paper simulations remain available for research. Real-money trading, exchange credentials, wallets, leverage, futures, and order endpoints are not implemented.

## Deployment

The production container copies the ledger into `/app/research/experiment_ledger.json`. A different ledger may be mounted and selected with `TRADEBOT_RESEARCH_LEDGER`, but it must pass the same validation rules.
