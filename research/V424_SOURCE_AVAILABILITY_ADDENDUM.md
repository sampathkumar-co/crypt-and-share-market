# v4.2.4 Binance Source-Availability Addendum

Status: frozen after source-only inventory and before any v4.2 model fit or verification outcome.

## Source-only finding

The original fold schedule is infeasible with the mandatory five-asset OI contract:

- successful source payloads: 6,689;
- missing daily metric payloads: 1,336;
- BTC complete dates: 1,461;
- ETH complete dates: 1,127;
- SOL complete dates: 1,122;
- XRP complete dates: 1,122;
- ADA complete dates: 1,127;
- common complete dates: 1,122, beginning 2021-12-01;
- longest consecutive common run: 2022-04-03 through 2024-12-31;
- first 200-day-warm feature date: 2022-10-19.

The original 2022-Q4 fold therefore had zero train and calibration rows. The campaign stopped before fitting a model or accessing a verification return.

## Replacement source-available schedule

Source coverage is extended through 2026-06-30. The model, features, costs, thresholds, execution, and breakthrough gates remain unchanged.

1. Train through 2023-03-31; calibrate 2023-Q2; verify 2023-Q3.
2. Train through 2023-09-30; calibrate 2023-Q4; verify 2024-Q1.
3. Train through 2024-03-31; calibrate 2024-Q2; verify 2024-Q3.
4. Train through 2024-09-30; calibrate 2024-Q4; verify 2025-Q1.
5. Train through 2025-03-31; calibrate 2025-Q2; verify 2025-Q3.

All training begins at the first valid dataset row after the frozen 200-day common-date warm-up. Each fold is expanding and no later fold data influences an earlier fold.

The period 2025-10-01 through 2026-06-30 remains sealed for a later confirmatory campaign and cannot influence five-fold selection.

The change is justified solely by public source availability, not by returns, predictions, trades, or model outcomes.
