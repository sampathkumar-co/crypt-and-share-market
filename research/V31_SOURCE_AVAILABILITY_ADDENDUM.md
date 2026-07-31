# v3.1 Source-Availability Addendum

Status: frozen before implementation and before any v3.1 verification outcome access.

The initial v3.1 protocol named January 1, 2017 as the Binance data start. BTCUSDT and ETHUSDT do not both have complete Binance spot history from that date. This addendum corrects that infeasible boundary before any implementation or result exists.

## Superseding rules

For v3.1 only, the following rules supersede conflicting statements in `V31_YIELD_TREND_OVERLAY_PROTOCOL.md`:

1. Required Binance crypto history begins September 1, 2017.
2. Feature warm-up is 200 completed calendar days.
3. Discovery remains entirely pre-verification and ends December 31, 2020.
4. Complete discovery quarters begin July 1, 2018 and end December 31, 2020, giving exactly 10 discovery quarters.
5. The discovery gate requires at least 8 positive quarters rather than 10, while every other discovery robustness gate remains unchanged.
6. The discovery model-ordering rule, five verification years, source series, cash accrual, feature formulas, costs, fills, exposure limits and breakthrough gates remain unchanged.
7. The implementation and report must fingerprint both the original protocol and this addendum.

No missing crypto bar may be fabricated, copied or interpolated. If either BTCUSDT or ETHUSDT is unavailable from September 1, 2017 onward, the run fails closed.
