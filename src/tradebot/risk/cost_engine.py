from dataclasses import dataclass
from tradebot.models import Market

@dataclass(frozen=True)
class CostConfig:
    crypto_exchange_fee_pct: float = 0.001
    crypto_slippage_pct: float = 0.001
    crypto_network_fee: float = 0.0
    equity_brokerage: float = 20.0
    equity_stt_pct: float = 0.00025
    equity_exchange_txn_pct: float = 0.0000325
    equity_sebi_pct: float = 0.000001
    equity_stamp_duty_pct: float = 0.00003
    equity_gst_pct: float = 0.18
    equity_slippage_pct: float = 0.0005

class CostEngine:
    def __init__(self, config: CostConfig | None = None): self.config = config or CostConfig()
    def estimate(self, market: Market, entry_price: float, exit_price: float, quantity: float) -> dict[str, float]:
        buy = entry_price * quantity; sell = exit_price * quantity; turnover = buy + sell
        if market == Market.CRYPTO:
            fees = turnover * self.config.crypto_exchange_fee_pct + self.config.crypto_network_fee
            slippage = turnover * self.config.crypto_slippage_pct
        else:
            brokerage = min(self.config.equity_brokerage, max(0, sell * 0.0003))
            txn = turnover * self.config.equity_exchange_txn_pct
            fees = brokerage + sell * self.config.equity_stt_pct + txn + turnover * self.config.equity_sebi_pct + buy * self.config.equity_stamp_duty_pct
            fees += (brokerage + txn) * self.config.equity_gst_pct
            slippage = turnover * self.config.equity_slippage_pct
        return {"fees": fees, "slippage": slippage, "total_cost": fees + slippage, "break_even_price": entry_price + (fees + slippage) / max(quantity, 1e-9)}
