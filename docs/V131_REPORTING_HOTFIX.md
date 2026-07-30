# v1.3.1 reporting-only hotfix

Workflow run `30506157335` completed the frozen external-data bundle and entered the one-shot holdout evaluator. The evaluator calculated all six portfolio variant summaries, comparisons and rejection reasons before crashing while formatting the report boundaries.

The crash was caused by two lines that unpacked `crypto_multifactor._period_bounds` into two values even though the frozen helper returns `(start_index, end_index, half_name)`.

No JSON report was written and no return values were observed before this note was committed. Under the frozen protocol, the holdout is considered consumed because the return-calculation step was entered and the summaries were computed.

The only permitted correction is a reporting wrapper that:

- calls the same frozen price loader, external store, variants, simulations, comparisons, costs, taxes, thresholds and reason checks;
- changes only the two boundary-unpacking statements to accept the helper's third value;
- writes the already-defined `MultiSourceHoldoutReport` schema; and
- leaves the original v1.3 evaluator byte-for-byte unchanged.

The deterministic rerun is evidence materialisation for the consumed holdout, not a new holdout or an opportunity to tune any rule. Passing or failing must be accepted exactly as produced.
