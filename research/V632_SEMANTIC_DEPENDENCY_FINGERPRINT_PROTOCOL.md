# v6.3.2 Semantic Dependency Fingerprint Protocol

Status: frozen before any v6.3 market outcome is calculated.

## Blocker

The artifact-corrected v6.3 workflow reproduced v6.1 and v6.2 with identical strategy economics, daily relative-return series, gates, DSR and rank results, but their whole-report SHA-256 values differed from earlier artifacts. The whole reports include upstream diagnostic report fingerprints and deterministic bootstrap seeds derived from those fingerprints. Those transport/evidence-lineage fields can change while the strategy path and substantive result remain identical.

The workflow failed closed before v6.3 ran.

## Required distinction

Two hashes are now mandatory:

1. **Whole-report self-hash** — proves the supplied JSON has not been altered. It is recalculated and must match the report's own `report_sha256`.
2. **Semantic evidence fingerprint** — identifies the actual frozen strategy evidence independently of volatile upstream report IDs.

Neither replaces the other.

## Frozen semantic projection

For v6.1 and v6.2, calculate SHA-256 over canonical JSON containing exactly:

- schema version;
- paper-only and authorization flags;
- exposed/untouched labels;
- final status;
- exact member count and member specifications;
- conservative economic metrics;
- complete material-gate map;
- complete statistical-gate map;
- each source's `standard_relative_series_sha256`;
- complete Deflated-Sharpe evidence;
- rank-stability summary excluding only the redundant full list of 35 percentile ranks.

Bootstrap numeric quantiles are not part of the semantic projection because their deterministic seed previously inherited volatile upstream report fingerprints. Their pass/fail map remains included through `statistical_gates`, and the exact daily source series that they resample remains cryptographically locked.

## Artifact-backed expected semantic fingerprints

From immutable artifacts:

- v6.1 artifact ID `8920755724`, report SHA `b6f5e75957cf31f26d7ebe2d1f341d67901dd4dfb3ad3d3f6b10a4be3fe34692`, semantic fingerprint `e88af5a6342f67c181abaea6e33c8f95a93117f00d26277698916a3dec414dc9`.
- v6.2 artifact ID `8920906642`, report SHA `7763dfbb68441e496ee638e23e7bd2650bf433a0d63a332d3b02c94709b60d7e`, semantic fingerprint `520bb66e8c84057317ed75be808697bbedc021ab385c5e55d85b4e63254f7b1b`.

## Permitted correction

- validate every dependency's own report self-hash;
- require the frozen semantic fingerprint and rejection status;
- record both the observed whole-report SHA and the artifact whole-report SHA;
- derive future v6.3 seeds from semantic fingerprints, protocol hashes and immutable source-series hashes, never from volatile upstream report IDs.

## Prohibited changes

No strategy member, target, source, timing, cost, metric, gate, trial count, evaluation interval or v6.3 mechanism may change. No prior evidence is deleted or rewritten.

## Safety

Paper-only, long-or-cash and non-authorizing. This evidence-identity correction cannot unlock live or continuous-paper trading.
