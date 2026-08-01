# v4.2 First Campaign Diagnosis

Report SHA-256: `7ab7336d0193d8bb77166f7b14fa5ae3e6801b5a8ffda5e393184a75561ed7d2`

Status: `NOT_YET_HISTORICAL_BREAKTHROUGH`.

## Result

- dataset rows: 6,715;
- complete feature dates: 1,343;
- first feature date: 2022-10-19;
- last feature date: 2026-06-22;
- five verification windows: 457 total days;
- standard return: 0.00%;
- stress return: 0.00%;
- costed target-changing actions: 0;
- every verification fold remained in cash.

This result does not authorize trading and does not displace v3.3 or v4.1.

## Mechanism diagnosis

The underlying labels were not empty:

- calibration meta-label base rate ranged from 22.6% to 30.9%;
- calibration downside-event rate ranged from 62.8% to 74.1%;
- 36 to 68 calibration rows per fold actually satisfied positive 3-day stress-net return, positive 7-day stress-net return, top-two rank, and no 2% path-loss event.

The learned hard vetoes failed to identify those intersections. The smallest calibration threshold set still produced zero costed actions in every fold.

Three fixed random seeds did not create genuine histogram-gradient-boosting diversity: the calibration disagreement threshold collapsed to roughly `5.6e-17`.

## Implementation finding

The first bundle artifact serialized local `__main__` dataclasses and could not be reloaded in a separate process. The implementation now persists primitive bundle state plus scikit-learn estimators and reconstructs dataclasses after loading.

## Decision

v4.2 is retained as a diagnosed zero-trade baseline. Its thresholds will not be loosened and its verification folds will not be reused as untouched evidence.

A separate v4.3 mechanism may use data through 2025-09-30 for development and calibration. The period 2025-10-01 through 2026-06-30 remains reserved for a preregistered, one-time evaluation.
