# v5.4.1 July 2026 Forward Paper-Smoke Result

## Outcome

The frozen one-day-delayed trend-acceleration mechanism passed every
predeclared July forward-smoke gate.

Status: `FORWARD_SMOKE_PASSED`

Report SHA-256:
`18a67cd667f5a68a2eb97b74c610c668e211bb99fd2683d0778d4700caf26ea3`

This is paper-only evidence and does not authorize live trading.

## Source integrity

- Common complete dates: 31 of 31.
- Successful archives: 470.
- Missing components: 0.
- July inventory SHA-256:
  `7c9a9e84a87174beb552109408385fa75c298d01985492936922d5ad4642e0fb`
- Daily spot, perpetual and metrics archives were combined with one monthly
  funding archive per asset under the frozen v5.4.1 repair.

No data was imputed and the five-asset universe remained unchanged.

## Performance

July 1 through July 31, 2026:

- v4.4 standard return: 0.5260090388%.
- Candidate standard return: 0.5266353321%.
- Standard excess: +0.0006262932%.
- v4.4 stress return: 0.4955706186%.
- Candidate stress return: 0.4987113766%.
- Stress excess: +0.0031407580%.
- Maximum drawdown did not worsen.
- One selected rebalance was attenuated, on July 4, 2026.

## Interpretation

This validates source completeness, execution safety and forward behavior for
one month. The economic edge is positive but extremely small and depends on a
single attenuation decision, so it is not a profitability breakthrough.

v5.3 still failed quarter consistency. The unchanged historical gates still
miss 5% annualized return, five positive windows and regime concentration.
Independent-source replication also remains missing.

The accepted strategy remains v4.4. The next valid step is to replay the frozen
July decisions against an independent spot-price source without changing the
signal or attenuation rule.
