# v5.4.1 July Funding Source Repair

## Reason

The initial v5.4 run stopped before simulation because Binance Vision does not
publish daily funding-rate archives at the frozen daily URL. All July spot,
perpetual and open-interest metric archives were retrieved successfully.

No candidate return, excess, activity decision or performance gate was
calculated in that run.

## Frozen repair

Replace only the unavailable funding source with one Binance Vision monthly
funding-rate archive per asset for July 2026. Parse that archive into its prior
calendar-day funding observations.

The repair may not change:

- candidate specification or one-day delay;
- July evaluation dates;
- minimum common-date requirement;
- cash treatment;
- execution costs, cadence, targets or universe;
- smoke gates or status interpretation.

No REST fallback, interpolation or alternative funding source is permitted.
If any monthly archive is missing or malformed, the result remains data
inconclusive.

The source-only availability check found the BTC July monthly archive before
this repair was committed; no market return was calculated.
