# v7.0 Intraday Alpha Lab — Frozen Research Protocol

Status: preregistered, paper-only, non-authorizing.

## Purpose

Test whether a small set of economically distinct hourly crypto mechanisms can produce repeatable net excess return after realistic costs. This programme is independent of v6.3/v6.4 and must not alter their code, evidence, dates, fingerprints, gates, or forward records.

The aspirational 1000% figure is not an acceptance criterion and cannot justify leverage, shorts, looser costs, selective reporting, or holdout reuse.

## Safety boundary

- Public market data only.
- Spot, long-or-cash accounting only.
- No live orders, exchange credentials, wallets, deposits, withdrawals, leverage, shorts, derivatives, borrowing, or recovery trading.
- `paper_only=true` and `authorizes_trading=false` are invariant.
- Maximum aggregate crypto exposure: 10%.
- Maximum single-asset exposure: 5%.
- Initial universe: BTC and ETH only.

## Decision and execution clock

- Signal frequency: completed 1-hour UTC bars.
- Earliest execution: next 5-minute bar open after the signal hour closes.
- Signal features must use only information timestamped at or before the completed signal hour.
- Missing or conflicting source data forces cash.

## Candidate families

Exactly four preregistered families enter the first tournament:

1. Cost-filtered hourly trend.
2. Post-shock reversal after stabilization.
3. Volatility breakout with confirmation.
4. BTC/ETH relative-strength rotation with absolute-trend veto.

No family may be added after outcome inspection without starting a new numbered protocol and increasing the permanent trial count.

## Cost model

Standard round-trip cost: 20 bps.
Stress round-trip cost: 40 bps.
Additional adverse execution tests: one-bar delay, doubled spread, and 5 bps extra slippage per side.

A trade is eligible only when its preregistered lower-confidence edge exceeds stress cost plus a 10 bps profit buffer.

## Evidence partitions

- Development: chronological training and calibration blocks.
- Walk-forward: at least 8 purged folds with a one-day embargo.
- Sealed historical holdout: one untouched interval selected before fitting.
- Independent-source replication: Binance and Coinbase.
- Prospective evidence: minimum 90 contiguous days and at least 60 genuine target-changing actions, whichever is later.

Consumed holdouts may never be reused for promotion.

## Promotion gates

A candidate is rejected unless all conditions pass:

- At least 7 of 8 standard-cost walk-forward folds positive.
- At least 6 of 8 stress-cost folds positive.
- Positive compounded excess return under standard and stress costs.
- Positive excess return in both chronological halves.
- Deflated Sharpe probability at least 0.95.
- Probability of Backtest Overfitting no greater than 0.20.
- Minimum track-record length satisfied.
- Independent-source replication passes without source-specific fitting.
- One-bar delayed execution remains positive after stress costs.
- Removing the best trade and best month leaves positive stress excess return.
- No trade contributes more than 15% of positive profit.
- No month contributes more than 30% of positive profit.
- Maximum drawdown no greater than 5% of total paper capital.
- At least 60 target-changing actions.

Ranking is lexicographic: gate survival, stress excess, drawdown, concentration, turnover, then standard excess. Raw return is never the first ranking key.

## Forward boundary

Historical success cannot authorize trading. A passing historical candidate may only unlock a new append-only sealed paper programme. Real-capital consideration would require a separate human-approved protocol after the complete prospective gate.
