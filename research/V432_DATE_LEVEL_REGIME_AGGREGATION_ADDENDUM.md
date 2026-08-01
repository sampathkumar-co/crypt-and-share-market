# v4.3.2 Date-Level Regime Aggregation Addendum

Status: frozen before any v4.3 model fit or sealed outcome.

Regime is a market-wide decision. For each completed decision date:

1. every recency classifier member predicts four-class probabilities for each of the five asset rows;
2. each member's probabilities are averaged across the five assets;
3. the date-level member probabilities are averaged across recency members;
4. panic thresholding and non-panic specialist selection use that date-level mean;
5. selected-regime probability disagreement is the standard deviation across the date-level recency-member probabilities.

The selected market regime applies to all five candidate assets on that date. No single asset's one-hot feature may independently select a different regime.
