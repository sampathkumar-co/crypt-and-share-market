# v6.3.1 v6.2 Artifact-Fingerprint Correction

Status: frozen before any v6.3 market outcome is calculated.

## Blocker

The first v6.3 workflow completed all focused tests, the full regression suite, both source reproductions and the v6.0 dependency chain. It then reproduced v6.1 and v6.2 before v6.3 was allowed to run.

v6.1 reproduced exactly. v6.2 reproduced report SHA:

`7763dfbb68441e496ee638e23e7bd2650bf433a0d63a332d3b02c94709b60d7e`

The v6.3 protocol and workflow incorrectly expected `e56c...`, which came from an erroneous reconstructed summary rather than the immutable v6.2 artifact. The workflow failed closed before calculating any v6.3 outcome.

## Authoritative v6.2 artifact

- workflow run: `30982969881`
- job: `92231257791`, named `consensus`
- artifact: `v62-consensus-30982969881`
- artifact ID: `8920906642`
- artifact digest: `sha256:2d1d5996ab009712c9d9d89bc4190f7f00f0ac5dd9758f84eb4a4d7f7ecc37c6`
- embedded `consensus-v62.json` report SHA: `7763dfbb68441e496ee638e23e7bd2650bf433a0d63a332d3b02c94709b60d7e`
- embedded status: `CONSENSUS_ENSEMBLE_REJECTED`

The later v6.3 dependency replay generated the same report SHA byte-for-byte. Therefore the code path is reproducible; the dependency constant was wrong.

## Permitted correction

- replace only the expected v6.2 dependency fingerprint in the v6.3 runner and workflow;
- preserve the exact v6.3 signal mechanism, evaluation dates, sources, costs, gates, seeds and trial count;
- record this correction's SHA in the v6.3 report;
- require v6.2 to reproduce both the authoritative SHA and rejection status before v6.3 runs.

## Prohibited changes

No target, member, source, threshold, coefficient, cost, timing rule, gate or evaluation outcome may change. No prior v6.2 report is rewritten. The mistaken `e56...` summary is superseded, not treated as evidence.

## Safety

Paper-only, long-or-cash and non-authorizing. This correction cannot unlock live or continuous-paper trading.
