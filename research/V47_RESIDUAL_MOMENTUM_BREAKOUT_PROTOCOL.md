# v4.7 Cross-Asset Residual Momentum and Breakout Protocol

## Purpose

v4.7 tests a return source that is structurally independent of the v4.3 learned utility model. It combines cross-asset residual momentum, multi-timeframe trend agreement, and volatility-compression breakout confirmation.

This generation is standalone. It must demonstrate broad walk-forward profitability and low concentration before any portfolio combination with v4.4 is considered.

## Safety boundary

- Paper-only and long-or-cash only.
- No live credentials or execution endpoints.
- Completed daily candles only.
- Decision after day D closes; fill at day D+1 open.
- Daily economic return is next-open to following-open.
- Maximum one selected asset.
- Target exposure is 5% of current equity.
- Rebalance cadence is three completed daily decisions.
- Standard and stress costs remain 10 and 20 basis points one way.
- Idle cash uses the verified v4.4 prior-day-known `DGS3MO` rule.

## Universe and factor removal

Universe remains BTC, ETH, SOL, XRP, and ADA.

For each asset and completed date:

- altcoin factor: BTC daily return;
- BTC factor: equal-weight daily return of ETH, SOL, XRP, and ADA;
- estimate 60-day beta from completed log returns;
- residual 60-day momentum equals asset 60-day return minus beta times factor 60-day return;
- residual 20-day momentum is computed analogously with the frozen 60-day beta;
- residual score is the average of residual 20-day and residual 60-day momentum, normalized by 60-day realized volatility.

This treatment permits BTC to compete in the same cross-sectional ranking while removing the dominant common-market component from every asset.

## Completed-date indicators

The implementation computes:

- spot returns over 7, 20, 60, and 120 days;
- 20-day and 50-day simple moving-average distances;
- 20-day path efficiency;
- 10-day and 60-day realized volatility;
- volatility-compression ratio, 10-day volatility divided by 60-day volatility;
- distance above the prior 20-day high, excluding the current candle;
- current volume divided by trailing 20-day average volume;
- cross-sectional residual-score percentile;
- BTC 100-day trend state;
- 50-day market breadth; and
- completed-date observable market regime for contribution diagnostics.

## Fixed market safety gate

Risk-on requires either:

- BTC close above its completed 100-day moving average; or
- at least 60% of assets above their completed 50-day moving averages.

Otherwise the strategy holds cash.

## Entry forms

### Continuation

An asset qualifies when:

- 20-day and 60-day returns are positive;
- close is above the 20-day and 50-day moving averages;
- residual 60-day momentum exceeds the configured floor;
- residual-score percentile exceeds the configured floor; and
- 20-day efficiency exceeds the configured floor.

### Compression breakout

An asset qualifies when:

- 60-day return is positive;
- residual 60-day momentum exceeds the configured floor;
- residual-score percentile exceeds the configured floor;
- compression ratio is at or below the configured ceiling;
- close exceeds the prior 20-day high by the configured breakout buffer; and
- volume ratio is at least 1.0.

The entry mode may be continuation-only, breakout-only, or either. A candidate satisfying both is labeled breakout for deterministic contribution diagnostics.

## Configuration grid

Only the following grid is allowed in the first campaign:

- residual 60-day floor: 0.00, 0.02, 0.04
- residual-score percentile floor: 0.60, 0.80
- 20-day efficiency floor: 0.20, 0.35, 0.50
- compression ceiling: 0.60, 0.80, 1.00
- breakout buffer: 0.00, 0.01
- entry mode: continuation, breakout, either

Total configurations: 324.

## Blocked selection folds

Threshold selection uses six non-overlapping pre-sealed quarters:

1. 2024-04-01 to 2024-06-30
2. 2024-07-01 to 2024-09-30
3. 2024-10-01 to 2024-12-31
4. 2025-01-01 to 2025-03-31
5. 2025-04-01 to 2025-06-30
6. 2025-07-01 to 2025-09-30

The five v4.3/v4.4 sealed windows are not used for threshold selection.

## Selection objective

For each configuration, simulate every fold at standard and stress cost with yield-bearing cash.

Eligibility requires:

- at least four positive standard folds;
- at least four positive stress folds;
- positive compounded standard and stress return;
- at least 20 costed target-changing actions across folds;
- BTC plus at least two non-BTC assets selected;
- maximum positive asset-contribution share at most 70%; and
- maximum positive fold-return share at most 70%.

Eligible configurations are selected lexicographically by:

1. highest worst standard fold return;
2. highest worst stress fold return;
3. highest positive standard-fold count;
4. highest positive stress-fold count;
5. highest compounded stress return;
6. highest compounded standard return;
7. lowest maximum drawdown;
8. lowest turnover;
9. deterministic conservative parameter order.

If no configuration is eligible, report the best lexicographic configuration as diagnostic-only and mark selection ineligible.

## Final retrospective evaluation

After selection is frozen, evaluate the unchanged five sealed windows with standard and stress costs.

Report all existing historical profitability, diversity, concentration, drawdown, and action gates; per-window and per-asset contributions; signal-form and observable-regime contributions; source and implementation fingerprints; and daily-return correlation with the v4.4 baseline.

A historical pass remains paper-only and requires later independent replication and genuinely future paper observation.
