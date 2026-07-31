# v4.0.1 Absolute-Return Composition Addendum

Status: frozen after the first v4.0 diagnostic run and before any corrected rerun.

## Observed implementation defect

The v4.0 return learner correctly predicted each asset's future excess return versus the equal-weight market. The decision governor then incorrectly compared that excess prediction alone with the full stress transaction cost.

The first diagnostic therefore produced zero eligible trades, zero turnover, and zero return. This outcome is not used to choose a profitable threshold. It exposed a dimensional mismatch: transaction costs apply to absolute asset return, not excess return alone.

## Frozen correction

Add a separate market-return ensemble trained chronologically on the future equal-weight market return over the same six-candle horizon.

For every asset decision:

`predicted_absolute_return = predicted_market_return + predicted_asset_excess_return`

The stress-cost gate then uses:

`predicted_net_edge = predicted_absolute_return - 40 basis points`

No cost, horizon, downside threshold, uncertainty method, asset universe, exposure limit, feature, split, or hyperparameter grid changes.

## Additional audit fields

Corrected reports must expose:

- predicted market return;
- predicted asset excess return;
- composed predicted absolute return;
- stress-cost net edge;
- market-ensemble disagreement;
- combined uncertainty used by the governor.

## Non-outcome tuning boundary

- The correction is required even if it makes historical returns worse.
- The observed zero P&L is not used to lower costs or relax the 45% downside threshold.
- The corrected ten-minute smoke loop still cannot prove profitability.
- A later long-span historical campaign remains required for the 90-day verification-span gate.

All original paper-only and isolation requirements remain unchanged.
