# v2.7 Result Validity Rules

Only reports satisfying every condition below are scientifically eligible:

1. `effective_state_assembly_warmup_hours` exists and equals `240`.
2. `fingerprints.runtime_guard_sha256` exists and is non-empty.
3. The deterministic `report_sha256` verifies after removing only that field.
4. The report lists exactly two frozen discovery windows and five frozen validation windows.
5. `paper_only` is true and both authorization flags are false.
6. The report never writes to or replaces Track A forward evidence.

Any report produced before merge commit `6973284d6129b3d1bd3297514e1781ff180a2204`, or any report missing the 240-hour field, is **INVALID DUE TO PROTOCOL/IMPLEMENTATION MISMATCH** and must not be used for threshold changes, mechanism selection, profitability claims or future evidence.

`results/latest.json` is authoritative only when it satisfies all rules above. Immutable invalid run files may remain for auditability but carry no scientific weight.
