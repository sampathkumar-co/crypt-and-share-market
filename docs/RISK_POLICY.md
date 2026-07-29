# Risk Policy

## Non-negotiable product boundary

- This repository is paper-trading research software only.
- It must never place, amend, or cancel a real order.
- It must not store or request broker credentials, exchange API secrets, wallet seeds, or private keys.
- It must not expose wallet, withdrawal, leverage, futures, options, or broker-order endpoints.
- It must not include a runtime flag that silently converts paper actions into live actions.
- Reports, dashboards, documentation, and demos must not promise profit or imply regulatory, investment, or tax approval.

## Research-integrity requirements

- Signals must use only information available before execution.
- A signal formed at a candle close must execute no earlier than the next available candle open.
- Gap behavior, slippage, fees, taxes, and ambiguous stop/target candles must be reported explicitly.
- The conservative intrabar outcome is the default when OHLCV data cannot reveal event ordering.
- Training and model selection must remain separated from unseen evaluation periods.
- Sample datasets and paper results must never be presented as evidence of live profitability.
- Every material strategy or execution change requires regression tests.

## Operational safeguards

- The dashboard must remain loopback-only by default and reject non-loopback binding.
- Dashboard file access must remain confined to approved project data directories.
- Request bodies and numeric inputs must be bounded.
- Public data access must be read-only and require no credentials.
- State and reports must not contain secrets.
- Automated CI must compile the source and run the complete test suite before merge.

## Financial and tax assumptions

- Brokerage, fees, slippage, taxes, and TDS are configurable estimates for comparative research.
- VDA TDS is a cash-flow withholding estimate and must not be double-counted as an additional economic loss when income tax is calculated.
- The engine does not model a user's full annual income, exemptions, surcharge, residency, loss set-off, turnover, filing position, or professional advice.
- Users must verify all financial, regulatory, and tax assumptions independently.

## Any future real-money work

Any real-money executor must be a separate repository, architecture, deployment, and review process. It would require independent security testing, legal and tax review, broker or exchange sandbox validation, explicit human approval, hard capital limits, immutable audit logging, and an external kill switch. No paper result from this repository authorizes live trading.
