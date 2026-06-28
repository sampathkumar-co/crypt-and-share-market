from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from tradebot.data.crypto_provider import PublicCryptoHistoricalClient
from tradebot.ml.crypto_signal_model import CryptoSignalModel
from tradebot.models import Action, Candle, Market, Position, Signal
from tradebot.risk.cost_engine import CostEngine
from tradebot.risk.risk_manager import RiskManager
from tradebot.risk.tax_engine import TaxEngine
from tradebot.scanner.crypto_scanner import ScannerConfig, evaluate_symbol


class CandleProvider(Protocol):
    def fetch_symbol(self, symbol: str, interval: str = "1m", days: int = 60) -> list[Candle]: ...


@dataclass
class LivePaperTrade:
    symbol: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    fees: float
    slippage_cost: float
    tax_estimate: float
    net_pnl: float
    entry_reason: str
    exit_reason: str


@dataclass
class LivePaperState:
    cash: float
    open_position: dict | None = None
    trade_history: list[dict] = field(default_factory=list)
    equity_history: list[dict] = field(default_factory=list)
    last_processed_timestamp: dict[str, str] = field(default_factory=dict)
    rejected_opportunities_count: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    loops_completed: int = 0


class PaperLiveCryptoBot:
    """Real-time paper simulation using public/read-only candles only; no orders or API keys."""

    def __init__(
        self,
        symbols: list[str],
        interval: str,
        cash: float,
        state_path: str | Path,
        model: CryptoSignalModel | None = None,
        provider: CandleProvider | None = None,
        scanner_config: ScannerConfig | None = None,
        lookback_candles: int = 60,
        max_holding_loops: int = 10,
    ):
        self.symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
        self.interval = interval
        self.initial_cash = cash
        self.state_path = Path(state_path)
        self.model = model
        self.provider = provider or PublicCryptoHistoricalClient()
        self.scanner_config = scanner_config or ScannerConfig(min_candles=30)
        self.lookback_candles = lookback_candles
        self.max_holding_loops = max_holding_loops
        self.histories: dict[str, list[Candle]] = {symbol: [] for symbol in self.symbols}
        self.risk = RiskManager()
        self.costs = CostEngine()
        self.tax = TaxEngine()
        self.state = self._load_state()

    def run(self, max_loops: int = 1, sleep_seconds: float = 60.0) -> LivePaperState:
        print("PAPER MODE ONLY - no real orders, wallets, exchange trading APIs, leverage, or API keys.")
        for loop_index in range(max_loops):
            summary = self.run_once()
            print(summary)
            if loop_index < max_loops - 1 and sleep_seconds > 0:
                time.sleep(sleep_seconds)
        return self.state

    def run_once(self) -> str:
        action = "skip"
        reason = "No accepted candidate."
        top_candidate = "-"
        timestamp = datetime.now(UTC).replace(tzinfo=None).isoformat()
        self._update_histories()
        latest_time = self._latest_time()
        if latest_time:
            timestamp = latest_time.isoformat()

        if self.state.open_position:
            exit_price, exit_reason = self._exit_decision(latest_time)
            if exit_reason:
                self._close_position(exit_price, timestamp, exit_reason)
                action = "exit"
                reason = exit_reason
            else:
                action = "hold"
                reason = "Open paper position remains active."

        if not self.state.open_position:
            candidates = []
            for symbol, candles in self.histories.items():
                if len(candles) < self.scanner_config.min_candles:
                    continue
                scan = evaluate_symbol(symbol, Market.CRYPTO, candles, self.scanner_config, model=self.model)
                if scan.rejected:
                    self.state.rejected_opportunities_count += 1
                    continue
                candidates.append(scan)
            candidates.sort(key=lambda result: result.combined_opportunity_score if result.combined_opportunity_score is not None else result.opportunity_score, reverse=True)
            if candidates:
                top = candidates[0]
                top_candidate = f"{top.symbol} score={top.combined_opportunity_score if top.combined_opportunity_score is not None else top.opportunity_score:.1f}"
                candle = self.histories[top.symbol][-1]
                risk_signal = Signal(Action.BUY, top.rank_score / 100.0, top.explanation, top.confidence, top.risk_score / 100.0)
                decision = self.risk.evaluate(Market.CRYPTO, self.state.cash, top.symbol, risk_signal, candle)
                if decision.approved:
                    cost = candle.close * decision.quantity
                    self.state.cash -= cost
                    self.state.open_position = {
                        "symbol": top.symbol,
                        "quantity": decision.quantity,
                        "entry_price": candle.close,
                        "stop_loss": decision.stop_loss,
                        "target": decision.target,
                        "entry_time": candle.timestamp.isoformat(),
                        "entry_loop": self.state.loops_completed,
                        "entry_reason": f"{top.explanation} ml_probability={top.ml_probability} ml_score={top.ml_score}",
                    }
                    action = "enter"
                    reason = self.state.open_position["entry_reason"]
                else:
                    self.state.rejected_opportunities_count += 1
                    reason = decision.reason

        equity = self._equity()
        self.state.equity_history.append({"timestamp": timestamp, "equity": equity, "cash": self.state.cash})
        self.state.loops_completed += 1
        self._save_state()
        open_position = self.state.open_position["symbol"] if self.state.open_position else "-"
        return f"{timestamp} cash={self.state.cash:.2f} equity={equity:.2f} open={open_position} top={top_candidate} action={action} reason={reason} warnings={'; '.join(self.state.warnings) or '-'}"

    def _update_histories(self) -> None:
        for symbol in self.symbols:
            try:
                candles = self.provider.fetch_symbol(symbol, interval=self.interval, days=self.lookback_candles)
                merged = {candle.timestamp: candle for candle in [*self.histories.get(symbol, []), *candles]}
                self.histories[symbol] = sorted(merged.values(), key=lambda candle: candle.timestamp)[-self.lookback_candles:]
                if self.histories[symbol]:
                    self.state.last_processed_timestamp[symbol] = self.histories[symbol][-1].timestamp.isoformat()
            except Exception as exc:
                self.state.errors.append(f"{symbol}: {exc}")

    def _exit_decision(self, latest_time: datetime | None) -> tuple[float, str]:
        if not self.state.open_position or latest_time is None:
            return 0.0, ""
        pos = self.state.open_position
        symbol = pos["symbol"]
        candle = self.histories.get(symbol, [])[-1] if self.histories.get(symbol) else None
        if candle is None:
            return 0.0, ""
        if candle.low <= pos["stop_loss"]:
            return pos["stop_loss"], "Stop loss hit"
        if candle.high >= pos["target"]:
            return pos["target"], "Target hit"
        if self.state.loops_completed - int(pos.get("entry_loop", 0)) >= self.max_holding_loops:
            return candle.close, "Max holding loops reached"
        if len(self.histories[symbol]) >= self.scanner_config.min_candles:
            scan = evaluate_symbol(symbol, Market.CRYPTO, self.histories[symbol], self.scanner_config, model=self.model)
            if scan.rejected or scan.risk_score >= 85.0:
                return candle.close, f"Scanner risk exit: {scan.rejection_reason or 'dangerous risk score'}"
        return 0.0, ""

    def _close_position(self, exit_price: float, timestamp: str, exit_reason: str) -> None:
        pos = self.state.open_position
        if not pos:
            return
        quantity = float(pos["quantity"])
        entry_price = float(pos["entry_price"])
        gross = (exit_price - entry_price) * quantity
        costs = self.costs.estimate(Market.CRYPTO, entry_price, exit_price, quantity)
        tax = self.tax.estimate(Market.CRYPTO, gross)["tax"]
        net = gross - costs["fees"] - costs["slippage"] - tax
        self.state.cash += entry_price * quantity + net
        self.state.trade_history.append(asdict(LivePaperTrade(pos["symbol"], pos["entry_time"], timestamp, entry_price, exit_price, quantity, gross, costs["fees"], costs["slippage"], tax, net, pos.get("entry_reason", ""), exit_reason)))
        self.state.open_position = None

    def _equity(self) -> float:
        if not self.state.open_position:
            return self.state.cash
        pos = self.state.open_position
        candles = self.histories.get(pos["symbol"], [])
        price = candles[-1].close if candles else float(pos["entry_price"])
        return self.state.cash + float(pos["quantity"]) * price

    def _latest_time(self) -> datetime | None:
        times = [candles[-1].timestamp for candles in self.histories.values() if candles]
        return max(times) if times else None

    def _load_state(self) -> LivePaperState:
        if not self.state_path.exists():
            state = LivePaperState(cash=self.initial_cash, warnings=["PAPER MODE ONLY: no live trading or order endpoints."])
            self._write_state(state)
            return state
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        return LivePaperState(**payload)

    def _save_state(self) -> None:
        self._write_state(self.state)

    def _write_state(self, state: LivePaperState) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
