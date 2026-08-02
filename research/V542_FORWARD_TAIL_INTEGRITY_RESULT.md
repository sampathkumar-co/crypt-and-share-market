# v5.4.2 Forward Tail Integrity Result

## Decision

Status: `FORWARD_TAIL_PASSED`.

The v5.4.1 result remains preserved as a valid July 1-23 partial smoke. v5.4.2 supersedes only its date-coverage claim by evaluating 30 genuine decision dates from July 1 through July 30.

## Integrity evidence

- Tail report SHA-256: `0219a929a5abf55dbfed719ecad7dbd90bdbda84cab3a2a3d9fb8f72206859d2`.
- Generic historical rows: 6,870.
- Shared rows: 6,870.
- Tail rows: 6,905.
- Feature vectors, one-day returns, feature names and row ordering matched exactly.
- Synthetic future labels were not used.
- Five August 1 Binance spot-open archives were complete and hashed.
## Corrected July smoke

- Decision dates: 2026-07-01 through 2026-07-30.
- Raw activity dates: 14.
- Delayed activity dates: 14.
- Attenuated selected rebalances: one, on 2026-07-04.
- Standard baseline return: 0.562927%.
- Standard candidate return: 0.563554%.
- Standard excess: +0.000627 percentage points.
- Stress baseline return: 0.522447%.
- Stress candidate return: 0.525589%.
- Stress excess: +0.003142 percentage points.
- Candidate drawdown did not worsen.
- Actions did not increase; no asset or target was added.

All frozen v5.4.2 gates passed. This validates forward safety and mechanism continuity, but the effect remains too small to satisfy the existing profitability gates. v4.4 remains the accepted strategy and no live trading is authorized.
