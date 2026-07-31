# v3.1.2 Scheduled-Rebalance Integrity Addendum

Status: frozen before any execution-corrected Binance outcome is calculated.

## Integrity finding

The v3.1 model declares a 10-day rebalance cadence and natural drift between scheduled rebalances. The inherited simulator nevertheless recomputes nominal target weights every day. Because current holdings drift after each return, comparing them with a freshly recomputed target creates small daily trades even when the scheduled rebalance is not due.

The old run charged those trades, so this is not a hidden-cost omission. It is an execution mismatch: daily volatility/exposure updates and drift rebalancing were introduced into a model declared to rebalance every 10 days.

## Frozen correction

For the exact model `sma100-rebalance10-top1-exposure10-vol2-brake20`:

1. Risk-off conditions are evaluated every day and may exit to cash at the next daily open.
2. Entry from cash uses the completed prior-day signal and fills at the next daily open.
3. While the trend regime remains valid and the scheduled rebalance is not due, current holdings and cash weights drift naturally with zero trade.
4. Asset ranking, volatility scaling, drawdown scaling and target exposure are recomputed only on entry or a due scheduled rebalance.
5. A due rebalance is costed using actual drifted weights versus the new target.
6. No silent exposure trim is permitted between scheduled rebalances.
7. Each annual window begins in cash and pays terminal liquidation cost.
8. Standard and stress costs remain 20 and 40 basis points round trip.

## Frozen audit sequence

- Keep the v3.1 selected model fixed; do not rerun model selection.
- Rerun the exact Binance BTCUSDT/ETHUSDT five-year verification with the corrected execution.
- Only if that audit passes may the same corrected execution be used by v3.2 Coinbase replication.

## Integrity gate

The corrected Binance audit passes only if:

- all five annual portfolio returns are positive at both cost levels;
- aggregate excess over identical H.15 cash is positive at both cost levels;
- at least four years are active;
- every active year has positive excess over cash at both cost levels;
- every inactive year equals cash within `1e-12` with zero turnover;
- total action days are at least 20;
- both BTC and ETH are selected;
- maximum drawdown is at most 5%;
- no asset or year supplies more than 80% of positive excess contribution;
- scheduled-rebalance tests prove there is zero turnover on non-due days when the regime and selected asset are unchanged.

## Scientific and safety boundary

This addendum corrects execution only. It does not change the model, assets, periods, rates, signal rules, costs, gates after outcome access, Track A evidence or authorization flags. It is historical, paper-only, long-or-cash, maximum 10% target crypto exposure, and cannot authorize trading or replace forward evidence.
