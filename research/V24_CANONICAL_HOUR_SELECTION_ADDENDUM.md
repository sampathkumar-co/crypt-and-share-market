# v2.4 Canonical Hour Selection Addendum

## Status

This addendum is frozen before v2.4 evaluator implementation and before any
v2.3 return, P&L, drawdown, benchmark result or confidence interval is read or
calculated. It is part of the v2.4 future-only evaluation protocol.

## Reason

Append-only collection may preserve more than one independently valid v2.0
capture for the same UTC hour. Existing files must never be deleted, rewritten
or retrospectively relabelled, but a scored campaign requires exactly one
canonical observation per hour.

## Frozen canonicalization rule

1. Validate every normalized snapshot and matching manifest independently using
   the pinned v2.0 schema and SHA-256 rules.
2. Group valid snapshots by exact `hour_bucket_utc`.
3. Within each hour, sort by `snapshot_id` ascending and select the first item.
   The compact UTC snapshot identifier therefore selects the earliest valid
   forward capture made in that hour.
4. A later valid capture remains immutable evidence but is non-canonical. It is
   never counted as another hour, used for entry/exit marks, or exposed to any
   discovery or holdout calculation.
5. Two files claiming the same `snapshot_id` with different bytes, a missing
   matching manifest, or a manifest/hash mismatch fail closed for that hour.
6. Every v2.3 decision input must reference the canonical snapshot identifier
   and record hash for its hour. A decision referencing a non-canonical duplicate
   is excluded rather than repaired.
7. A missing canonical hour breaks continuity. No cash decision, price or return
   is fabricated for it.

## Pinned-support boundary

v2.4 may reuse the pinned v2.2 JSON, hashing, UTC parsing, manifest and v2.0
snapshot-validation helpers. It must not reuse a duplicate-rejecting hourly
resolver as the final v2.4 selector. Canonical grouping and earliest-valid
selection are implemented in v2.4 and fingerprinted together with this addendum.

## Safety and holdout boundary

Canonicalization may report filenames, hours, validation exclusions and hashes
only. Before the full readiness requirement is met it may not read or disclose
returns, P&L, drawdown, benchmark values, confidence intervals or holdout
outcomes. All outputs remain paper-only with `authorizes_trading=false`.
