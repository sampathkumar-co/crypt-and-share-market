# v4.3 First Sealed Campaign Diagnosis

Report SHA-256: `27ddd3cddd845e0e9611fce72441b83499a83568f6308f7c57edf644fd7319b3`

Status: `NOT_YET_HISTORICAL_BREAKTHROUGH`.

## Aggregate result

- standard return: +0.4287%;
- stress return: +0.1069%;
- annualized standard return: 0.5909%;
- maximum drawdown: 1.1747%;
- costed target-changing actions: 51;
- all five assets selected;
- maximum positive asset share: 41.99%;
- verification days with complete features: 265.

The portable bundle was independently reloaded in a separate Python process. It contains primitive state plus estimators and does not depend on `__main__` dataclasses.

## Sealed windows

1. +0.3663% standard, +0.3257% stress;
2. +0.4799% standard, +0.3991% stress;
3. -0.6678% standard, -0.7368% stress;
4. +0.5736% standard, +0.4724% stress;
5. -0.3181% standard, -0.3478% stress.

Three of five windows were positive at both cost levels. The five-positive-standard, four-positive-stress, 5%-annualized, and regime-concentration gates failed. Drawdown, activity, asset diversity, asset concentration, window concentration and aggregate stress-return gates passed.

## Structural finding

The model deliberately holds at least 90% cash, but the v4.3 ledger credits that cash with zero return. Therefore a 5% total-portfolio annualized gate requires unusually high returns from the maximum 10% crypto sleeve. This differs from the verified v3.1.2/v3.2 baseline, where unallocated capital earns the exact public three-month Treasury cash return.

## Decision

v4.3 is retained as the first active, diversified learned baseline. Its sealed windows are now exposed and will not be reused as untouched evidence for a modified model.

The next research generation will preserve v4.3 signals and risk limits while adding the same independently sourced yield-bearing cash accounting used by the verified baseline. It will be labelled retrospective on previously exposed dates and must rely on forward paper evidence for promotion.
