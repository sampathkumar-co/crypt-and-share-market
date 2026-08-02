# v5.4 July Forward Paper-Smoke Implementation Contract

## Frozen provenance

- v5.2 report:
  `60f4d1b88dc0ef66d64a8ec4e192a56fdaf76a07182bde8e1567f17a61313ab2`
- v5.3 report:
  `133917e3b52367d34b51ca5f7958d3cbe1f982903669570140937b55be7197ea`
- v5.3 result commit: `1fca708`.
- Candidate is the exact v5.2 primary shifted forward one daily date.

The implementation must reject any specification mismatch before downloading
or evaluating July data.

## July source loader

For each asset and each July date, request these Binance Vision archives:

- spot daily 1d kline;
- USD-M perpetual daily 1d kline;
- USD-M daily funding-rate archive;
- USD-M daily metrics archive.

Use the existing parsers and SHA-256 inventory format. Missing or malformed
components are recorded; they are never imputed. A date is eligible only when
all four components exist for all five assets.

## Evaluation

Merge eligible July states into the frozen through-June state history, rebuild
the dataset, and generate the primary activity continuously with sufficient
prior history. Shift activity forward by one daily date before mapping it to
the v4.8 attenuation adapter.

Evaluate one continuous window from the first eligible July date through the
last eligible July date under standard and stress costs. Reuse the unchanged
cash history; its last prior-known rate may carry forward.

No secondary mechanism, parameter neighbor or alternative delay is evaluated.

## Required result states

- `FORWARD_SMOKE_DATA_INCONCLUSIVE`: fewer than 29 common complete dates.
- `FORWARD_SMOKE_NO_SIGNAL`: data complete but no attenuation decision.
- `FORWARD_SMOKE_PASSED`: every frozen smoke gate passes.
- `FORWARD_SMOKE_FAILED`: at least one performance or safety gate fails.

The JSON report must include every source URL/hash, missing component, common
date, activity date, baseline/candidate simulation, gate result and provenance
hash. It must state `paper_only: true` and `authorizes_trading: false`.
