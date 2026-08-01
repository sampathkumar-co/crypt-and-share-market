# v4.2.3 Exact Feature Semantics Addendum

Status: frozen before any v4.2 model fit or historical outcome.

- Basis annualization is `365 * (perpetual_close / spot_close - 1)`.
- Funding sign persistence is the absolute mean sign over the latest 30 completed daily funding sums.
- Price/open-interest interaction uses completed 7-day spot return and 7-day open-interest change:
  - long build-up: both positive;
  - short build-up: price negative and open interest positive;
  - long liquidation: both negative;
  - short covering: price positive and open interest negative.
- Completed recovery state is 30-day spot return below -8% with positive 7-day spot return.
- Cross-sectional percentile ranks use stable ascending ranks from 0 to 1 across the five assets for 30-day spot momentum, 7-day basis change, current funding, 7-day open-interest change, 30-day volatility, and current spot flow.
- The top-two meta-label equals one only when the asset is in the highest two realized 3-day returns and its 3-day return exceeds the frozen 40-basis-point stress round trip.
- Regime prediction is the majority vote of the three fixed-seed regime classifiers; ties select the lowest numeric regime code.
- Ensemble disagreement is the root-sum-square of within-ensemble standard deviations for specialist 3-day return, specialist 7-day return, specialist rank, meta probability, and downside probability.
- The calibration disagreement threshold is the 75th percentile across complete calibration rows.
- Every feature row requires 200 consecutive completed common dates and eight consecutive future dates for labels; a gap closes that row.

No verification outcome may modify these semantics.
- Eligible candidates are ordered first by descending specialist predicted cross-sectional rank and then by descending frozen specialist score; asset symbol is the final deterministic tie-break.
- A panic signal resets the three-day cadence only when it exits a nonzero holding; repeated panic observations while already in cash do not postpone the next scheduled decision indefinitely.
- Terminal-liquidation cost is included in final equity and maximum drawdown, while terminal liquidation remains excluded from target-changing action count.
