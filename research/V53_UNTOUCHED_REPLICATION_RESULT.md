# v5.3 Untouched Nine-Month Replication Result

## Decision

The frozen primary mechanism failed the predeclared untouched-replication gate.
No parameter was changed after the untouched result was calculated.

Report SHA-256:
`133917e3b52367d34b51ca5f7958d3cbe1f982903669570140937b55be7197ea`

Status:
`UNTOUCHED_MECHANISM_REPLICATION_FAILED`

The accepted strategy remains v4.4. No live trading is authorized.

## Primary continuous result

October 1, 2025 through June 30, 2026:

- Standard excess over v4.4: +0.0664830836%.
- Stress excess over v4.4: +0.1072374682%.
- Twenty attenuated decisions in the continuous simulation.
- One-day-delay standard excess: +0.3405576849%.
- All safety, action, drawdown and loss-floor gates passed.

The only failed primary replication gate was requiring at least two of three
calendar quarters to have positive standard excess.

## Calendar-quarter excess

Standard / stress excess:

- 2025-Q4: +0.0941597515% / +0.1016315873%.
- 2026-Q1: -0.0106494611% / +0.0069801874%.
- 2026-Q2: -0.0500632186% / -0.0348176507%.

Only one quarter was strictly positive under standard costs, versus the frozen
requirement of two. The negative quarters remained well inside the -0.25%
loss floor, but the consistency rule is binding and cannot be waived.

## Existing five-window profitability evaluation

The overlay changed the frozen v4.4 result as follows:

- Standard return: 3.0967689983% to 3.1845104707%.
- Stress return: 2.7664740859% to 2.8977065383%.
- Annualized standard return: 4.2901252068% to 4.4123951646%.
- Maximum drawdown: 1.0543649640% to 0.9893231912%.
- Attenuated decisions: 21.

It still failed annualized return of at least 5%, five positive standard
windows and regime-concentration requirements. Independent-source replication
and a current-market smoke test also remain absent.

## Secondary corroboration

The frozen breadth-reversal mechanism failed clearly:

- Continuous standard excess: -0.0881615756%.
- Continuous stress excess: -0.0671666814%.
- One-day-delay standard excess: -0.1428333330%.

It cannot replace or rescue the primary mechanism.

## Research implication

The primary contains real downside-control value, but same-day intervention is
not stable across quarters. Its one-decision-day delayed variant was positive
in the original v5.2 attacks and materially stronger in the untouched period.
That observation is now exposed and may only motivate a newly frozen,
forward-only version. It cannot retroactively repair v5.3.
