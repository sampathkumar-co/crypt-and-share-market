# v3.1.1 Federal Reserve H.15 Transport Addendum

Status: frozen before any v3.1 verification outcome is accessed.

## Blocker

The v3.1 model, costs, periods and gates are frozen, but both FRED graph CSV URLs can stall or return transient server failures. The blocked run has not validated or persisted a five-year outcome report.

## Authoritative fallback

The fallback is the Board of Governors of the Federal Reserve System H.15 Data Download Program preformatted Treasury Constant Maturities package:

- release: H.15 Selected Interest Rates;
- exact daily series identifier: `H15/H15/RIFLGFCM03_N.B`;
- description: market yield on U.S. Treasury securities at 3-month constant maturity, quoted on an investment basis;
- units: percent per year;
- frequency: business day;
- direct package URL is frozen in the implementation.

This is the same underlying H.15 series represented by FRED code `DGS3MO`, not a Treasury-bill proxy or a different maturity.

## Frozen transport order

1. Try the dated FRED DGS3MO CSV once with a 15-second timeout.
2. Try the undated FRED DGS3MO CSV once with a 15-second timeout.
3. Download the Federal Reserve Board H.15 package up to two times with a 30-second timeout.
4. Extract only `Time Period` and `RIFLGFCM03_N.B`.
5. Convert those rows to the existing `observation_date,DGS3MO` parser contract.
6. Fail closed if required dates are absent, duplicated or invalid.

## Audit requirements

The report must record:

- every attempted URL and outcome;
- selected source and exact series identifier;
- raw-source SHA-256;
- normalized cash-series SHA-256;
- observation count, first date and last date;
- parser and transport fingerprints.

## Scientific boundary

This addendum changes transport only. It does not change:

- the 64-model grid;
- discovery or verification periods;
- daily rates or accrual formula;
- next-open fills;
- crypto transaction costs;
- exposure limits;
- model selection;
- profitability gates;
- Track A evidence;
- trading authorization.

The workflow concurrency key may be moved to a v3.1.1-specific group so the corrected run is not blocked by the already-stalled v3.1 transport run.
