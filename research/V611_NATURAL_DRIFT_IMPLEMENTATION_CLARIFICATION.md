# v6.1.1 Natural-Drift Implementation Clarification

Status: frozen after a synthetic invariant test exposed an ambiguity and before any v6.1 market outcome was calculated.

## Observed blocker

The first v6.1 workflow stopped in focused synthetic tests before downloading or evaluating the v6.1 ensemble market result. A member entered at a valid 10% target and then appreciated. On the following non-due day its naturally drifted portfolio weight exceeded 10%, causing the implementation to reject the carried state.

## Correct interpretation

The existing corrected v3.1.2 execution contract limits **newly set target exposure** to 10%. It intentionally preserves natural portfolio-weight drift between scheduled target-changing decisions.

Therefore v6.1 applies the same semantics:

- every member's newly set scheduled target is at most 10%;
- every risk-off target is zero;
- a non-due member carries its naturally drifted weights without off-cadence normalization;
- the ensemble target is the arithmetic mean of the 16 member states;
- the ensemble is not rescaled merely because appreciation has lifted its naturally drifted exposure above 10%;
- no member or aggregate sleeve may add exposure off cadence;
- costs are charged only for actual aggregate target changes;
- active daily rescaling back to 10% is prohibited because it would recreate the unintended daily-rebalancing defect corrected by v3.1.2.

## Safety boundary

This clarification does not increase any newly initiated exposure above the frozen 10% target, change a member, alter an ensemble weight, relax a profitability gate, or use a market outcome. It resolves execution semantics only and preserves paper-only, long-or-cash operation.
