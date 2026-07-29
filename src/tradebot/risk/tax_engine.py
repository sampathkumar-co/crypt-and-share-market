from __future__ import annotations

from dataclasses import dataclass

from tradebot.models import Market


@dataclass(frozen=True)
class TaxConfig:
    """Configurable, simplified Indian tax estimates for paper research.

    Defaults intentionally exclude surcharge and cess so existing research runs remain
    comparable. Set ``include_cess=True`` when a closer cash-tax estimate is desired.
    The engine is not a tax calculator and cannot model a user's total annual income,
    exemptions, loss set-off restrictions, residency, or filing position.
    """

    crypto_gain_tax_pct: float = 0.30
    crypto_tds_pct: float = 0.01
    equity_stcg_pct: float = 0.20
    equity_ltcg_pct: float = 0.125
    cess_pct: float = 0.04
    include_cess: bool = False
    simple_mode: bool = True


class TaxEngine:
    def __init__(self, config: TaxConfig | None = None):
        self.config = config or TaxConfig()

    def estimate(
        self,
        market: Market,
        gross_pnl: float,
        holding_days: int = 0,
        *,
        exit_value: float | None = None,
    ) -> dict[str, float | str]:
        """Estimate tax and separately report TDS cash-flow withholding.

        VDA TDS is based on transfer consideration, not profit. It is therefore
        reported separately and is not treated as an additional trading loss by the
        backtest. Callers should pass ``exit_value`` for a meaningful TDS estimate.
        """

        tds_cashflow = 0.0
        if market == Market.CRYPTO and exit_value is not None:
            tds_cashflow = max(0.0, exit_value) * self.config.crypto_tds_pct

        if gross_pnl <= 0:
            return {
                "tax": 0.0,
                "tds_cashflow": tds_cashflow,
                "note": "No income-tax estimate on a losing trade in simplified mode; VDA TDS may still affect cash flow.",
            }

        if market == Market.CRYPTO:
            rate = self.config.crypto_gain_tax_pct
            note = "Simplified estimate on positive VDA gain; no loss set-off or personal tax circumstances modelled."
        else:
            rate = self.config.equity_ltcg_pct if holding_days > 365 else self.config.equity_stcg_pct
            note = "Simplified listed-equity capital-gains estimate; annual exemption and personal tax circumstances are not modelled."

        base_tax = gross_pnl * rate
        cess = base_tax * self.config.cess_pct if self.config.include_cess else 0.0
        return {
            "tax": base_tax + cess,
            "tds_cashflow": tds_cashflow,
            "note": note,
        }
