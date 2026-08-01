# v4.2.2 Regime-Specialist Model Structure Addendum

Status: frozen before any v4.2 model fit or historical outcome.

For each available long regime—trend, recovery, and chop—the specialist contains three fixed-seed histogram-gradient-boosting ensembles for:

- 3-day absolute next-open return;
- 7-day absolute next-open return;
- 3-day cross-sectional net-return rank.

The specialist is trained only on rows labelled with its regime. It is unavailable when the training fold has fewer than 250 specialist rows.

Separate global three-seed classifier ensembles are trained for:

- the top-two-and-positive-stress-net meta-label;
- three-day path downside worse than 2%;
- market regime.

The predicted regime selects one specialist. Panic or an unavailable specialist produces cash. The specialist rank prediction orders eligible assets; predicted 3-day return, predicted 7-day return, meta probability, downside probability, and ensemble disagreement apply the previously frozen score and vetoes.

No v4.1 outcome or v4.2 verification result may alter this structure.