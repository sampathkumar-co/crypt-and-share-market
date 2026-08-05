# v6.0.1.1 Coinbase Delay-Warmup Addendum

Status: frozen before the delayed-execution outcome is calculated.

## Blocker

The frozen v3.2 Coinbase history begins on 2020-06-14. That is exactly sufficient to construct the 2020-12-31 feature used by the original 2021-01-01 decision, but the preregistered one-additional-day execution-delay diagnostic requires the 2020-12-30 feature.

## Permitted repair

Fetch exactly one additional completed Coinbase daily candle for each frozen product:

- BTC-USD on 2020-06-13;
- ETH-USD on 2020-06-13.

The public Coinbase Exchange candle endpoint, daily granularity, parser, validation rules and retry policy remain unchanged. Raw-response SHA-256, normalized candle SHA-256, product, requested date and URL must be recorded in the v6.0.1 report.

## Prohibited changes

- The v3.2 authoritative report, source inventory and original control interval are not rewritten.
- The strategy model, parameters, assets, cash series, costs, cadence, exposure and evaluation dates remain unchanged.
- The extra candles may be used only as historical warmup to form the D-2 feature for the delayed diagnostic.
- No missing candle may be synthesized or forward-filled.
- Failure to obtain either genuine candle fails the delayed diagnostic closed.

## Exact-control requirement

After adding the warmup candles, the lag-one control must still reproduce every frozen v3.2 annual and aggregate result within `1e-12`. The delayed result remains unknown when this addendum is committed.
