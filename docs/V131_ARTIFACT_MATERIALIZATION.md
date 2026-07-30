# v1.3.1 artifact materialisation note

Workflow run `30506566985` successfully rebuilt and verified the external bundle, ran the consumed holdout, and wrote the complete JSON report. The frozen result was rejected and all strategy variants remained in cash for all three periods.

The following post-run validation step failed only because its Markdown summary referenced two convenience fields that were not members of the frozen report dataclass:

- `primary_average_improvement_vs_raw`; and
- `primary_beats_raw_periods`.

The underlying report already contains everything required to derive them:

- variant-level average returns;
- `primary_beats_raw_fraction`; and
- the exact three primary periods.

The only permitted follow-up is an artifact-materialisation wrapper that adds those two derived display fields to the serialized payload. It must call the same reporting-hotfix evaluator and cannot modify the accepted flag, reasons, variants, periods, source hashes, prices, factors, costs, taxes or thresholds.

The deterministic rerun is solely to upload the consumed holdout evidence. The rejected decision is final and no parameter or factor may be changed on this holdout.
