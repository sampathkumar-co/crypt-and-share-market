# Profit-quality selection research (v0.5)

Version 0.5 is an experimental layer on top of the v0.4 historical research gate. It does not add more indicators or increase trading frequency. Its purpose is to reduce parameter-selection overfitting and keep the strategy in cash when training evidence is fragile.

## Why this exists

The v0.4 gate optimizes a candidate on the full training window and then freezes it for the following unseen window. That is materially better than tuning on unseen data, but a candidate can still win the training score because of one unusually favourable subperiod.

Version 0.5 asks a harder question:

> Did the candidate work consistently across several chronological parts of the training history, and did that stability-aware selection improve the following unseen result compared with simply choosing the highest full-training score?

## Two-stage selection

The selector uses a bounded compute budget:

1. Run the full candidate grid on the complete training window.
2. Keep only the highest-scoring candidates within the stability-screen budget.
3. Re-evaluate those candidates on contiguous, non-overlapping chronological training folds.
4. Reject candidates that depend on one lucky fold, have excessive return dispersion, have too few trades, or have a materially negative worst fold.
5. Select the best stability-adjusted candidate, or explicitly abstain and remain in cash.

The default screen evaluates 24 candidates in detail after the initial grid, preventing the stability test from multiplying the entire search cost.

## Default stability requirements

A candidate is ineligible when any of these conditions is true:

- fewer than two usable chronological training folds are available;
- full-training net return is not positive after modelled costs and tax;
- fewer than 67% of training folds are profitable;
- the worst training fold loses more than 3%;
- fold-return dispersion exceeds 8%;
- the folds contain fewer than two completed trades in total.

These are selection rules, not claims of future profitability.

## Direct comparison with v0.4 selection

For every independent unseen period, the report evaluates both:

- **naive selection**: the candidate with the highest full-training score, matching the existing selection style;
- **stability-aware selection**: the best eligible candidate after fold testing, or cash when none qualifies.

The report records unseen net return, drawdown, abstentions, improvement over naive selection, and how often the stability-aware choice wins.

## Run it

```bash
tradebot-profit-quality \
  --folder data/crypto \
  --market crypto \
  --train-size 180 \
  --test-size 60 \
  --max-candidates 120 \
  --screen-candidates 24 \
  --json-out reports/profit_quality_gate.json
```

The command exits with:

- `0` when at least one strategy proves positive average unseen return and improves over naive selection often enough;
- `2` when no strategy passes the profit-quality comparison;
- another non-zero value for invalid input or execution failure.

## Safety boundary

A passing v0.5 result permits only another forward paper comparison. It does not authorize real-money trading. The report explicitly sets `authorizes_real_trading` to `false`.
