from dataclasses import dataclass
from tradebot.models import Market

@dataclass(frozen=True)
class TaxConfig:
    crypto_gain_tax_pct: float = 0.30
    crypto_tds_pct: float = 0.01
    equity_stcg_pct: float = 0.15
    equity_ltcg_pct: float = 0.10
    simple_mode: bool = True

class TaxEngine:
    def __init__(self, config: TaxConfig | None = None): self.config = config or TaxConfig()
    def estimate(self, market: Market, gross_pnl: float, holding_days: int = 0) -> dict[str, float]:
        if gross_pnl <= 0:
            return {"tax": 0.0, "tds_cashflow": 0.0, "note": "No tax estimated on losing trade in simple mode"}
        if market == Market.CRYPTO:
            return {"tax": gross_pnl * self.config.crypto_gain_tax_pct, "tds_cashflow": gross_pnl * self.config.crypto_tds_pct, "note": "Estimate: 30% tax on positive crypto gains; losses not offset in simple mode"}
        rate = self.config.equity_ltcg_pct if holding_days >= 365 else self.config.equity_stcg_pct
        return {"tax": gross_pnl * rate, "tds_cashflow": 0.0, "note": "Estimate only, not tax advice"}
