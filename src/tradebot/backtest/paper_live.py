from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from tradebot.backtest.regime import RegimeFilterConfig, classify_regime, regime_allows_strategy
from tradebot.backtest.research_gate import validate_forward_gate
from tradebot.backtest.walk_forward import build_strategy
from tradebot.data.crypto_provider import PublicCryptoHistoricalClient
from tradebot.ml.crypto_signal_model import CryptoSignalModel
from tradebot.models import Action, Candle, Market, Signal
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
    tds_cashflow: float = 0.0
    holding_loops: int = 0


@dataclass
class LivePaperState:
    cash: float
    open_position: dict | None = None
    pending_entry: dict | None = None
    trade_history: list[dict] = field(default_factory=list)
    equity_history: list[dict] = field(default_factory=list)
    last_processed_timestamp: dict[str, str] = field(default_factory=dict)
    rejected_opportunities_count: int = 0
    regime_filtered_count: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    loops_completed: int = 0
    last_exit_loop: int = -1_000_000


class PaperLiveCryptoBot:
    """Public-data paper simulation. Continuous mode requires a passing gate report."""

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
        max_holding_loops: int = 20,
        min_holding_loops: int = 2,
        cooldown_loops: int = 2,
        exit_confirmation_loops: int = 2,
        trailing_stop_pct: float = 0.03,
        breakeven_trigger_pct: float = 0.02,
        strategy_name: str = "momentum",
        regime_filter: RegimeFilterConfig | None = None,
    ):
        self.symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
        if not self.symbols:
            raise ValueError("At least one crypto symbol is required")
        if cash <= 0:
            raise ValueError("cash must be positive")
        if lookback_candles < 5:
            raise ValueError("lookback_candles must be at least 5")
        if max_holding_loops < 1:
            raise ValueError("max_holding_loops must be positive")
        if min_holding_loops < 0 or min_holding_loops > max_holding_loops:
            raise ValueError("min_holding_loops must be between 0 and max_holding_loops")
        if cooldown_loops < 0 or exit_confirmation_loops < 1:
            raise ValueError("cooldown must be non-negative and exit confirmation positive")
        if not 0 < trailing_stop_pct < 1:
            raise ValueError("trailing_stop_pct must be between 0 and 1")
        if not 0 <= breakeven_trigger_pct < 1:
            raise ValueError("breakeven_trigger_pct must be between 0 and 1")

        self.interval = interval
        self.initial_cash = cash
        self.state_path = Path(state_path)
        self.model = model
        self.provider = provider or PublicCryptoHistoricalClient()
        self.scanner_config = scanner_config or ScannerConfig(min_candles=30)
        self.lookback_candles = lookback_candles
        self.max_holding_loops = max_holding_loops
        self.min_holding_loops = min_holding_loops
        self.cooldown_loops = cooldown_loops
        self.exit_confirmation_loops = exit_confirmation_loops
        self.trailing_stop_pct = trailing_stop_pct
        self.breakeven_trigger_pct = breakeven_trigger_pct
        self.strategy_name = strategy_name
        self.strategy = build_strategy(strategy_name)
        self.regime_filter = regime_filter or RegimeFilterConfig()
        self.continuous_authorized = False
        self.histories: dict[str, list[Candle]] = {symbol: [] for symbol in self.symbols}
        self.risk = RiskManager()
        self.costs = CostEngine()
        self.tax = TaxEngine()
        self.state = self._load_state()

    def run(self, max_loops: int = 1, sleep_seconds: float = 60.0) -> LivePaperState:
        if max_loops < 1:
            raise ValueError("max_loops must be positive")
        if sleep_seconds < 0:
            raise ValueError("sleep_seconds cannot be negative")
        print("PAPER MODE ONLY - no real orders, wallets, exchange trading APIs, leverage, or API keys.")
        for index in range(max_loops):
            print(self.run_once())
            if index < max_loops - 1 and sleep_seconds > 0:
                time.sleep(sleep_seconds)
        return self.state

    def run_continuous(
        self,
        *,
        sleep_seconds: float,
        gate_report_path: str | Path,
        gate_max_age_days: int = 90,
    ) -> LivePaperState:
        if sleep_seconds <= 0:
            raise ValueError("continuous mode requires positive sleep_seconds")
        gate = validate_forward_gate(
            gate_report_path,
            strategy_name=self.strategy_name,
            market=Market.CRYPTO,
            max_age_days=gate_max_age_days,
        )
        self.continuous_authorized = True
        self.state.warnings.append(
            f"Continuous forward paper authorized by gate "
            f"{str(gate.get('dataset_fingerprint', ''))[:12]} for {self.strategy_name}."
        )
        self._save_state()
        print("CONTINUOUS PAPER MODE ONLY - historical gates passed; no real orders are possible.")
        while True:
            print(self.run_once())
            time.sleep(sleep_seconds)

    def run_once(self) -> str:
        action = "skip"
        reason = "No accepted candidate."
        top_candidate = "-"
        timestamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        self._update_histories()
        latest_time = self._latest_time()
        if latest_time:
            timestamp = latest_time.isoformat()

        if self.state.open_position:
            exit_price, exit_reason = self._exit_decision()
            if exit_reason:
                self._close_position(exit_price, timestamp, exit_reason)
                action, reason = "exit", exit_reason
            else:
                action, reason = "hold", "Open paper position remains active."

        if not self.state.open_position and self.state.pending_entry:
            entered, pending_reason = self._execute_pending_entry()
            if entered:
                action = "enter"
            reason = pending_reason or reason

        cooldown_complete = (
            self.state.loops_completed - self.state.last_exit_loop > self.cooldown_loops
        )
        if not self.state.open_position and not self.state.pending_entry and cooldown_complete:
            candidates = self._candidates()
            if candidates:
                top = candidates[0]
                score = top.combined_opportunity_score if top.combined_opportunity_score is not None else top.opportunity_score
                top_candidate = f"{top.symbol} score={score:.1f}"
                candle = self.histories[top.symbol][-1]
                if self.continuous_authorized:
                    self.state.pending_entry = {
                        "symbol": top.symbol,
                        "signal_time": candle.timestamp.isoformat(),
                        "entry_reason": self._entry_reason(top),
                    }
                    action, reason = "queue", "Signal queued until a newly observed candle exists."
                else:
                    entered, reason = self._enter(top, candle, candle.close)
                    if entered:
                        action = "enter"

        equity = self._equity()
        self.state.equity_history.append({"timestamp": timestamp, "equity": equity, "cash": self.state.cash})
        self.state.loops_completed += 1
        self._save_state()
        opened = self.state.open_position["symbol"] if self.state.open_position else "-"
        return (
            f"{timestamp} cash={self.state.cash:.2f} equity={equity:.2f} open={opened} "
            f"top={top_candidate} action={action} reason={reason} "
            f"warnings={'; '.join(self.state.warnings) or '-'}"
        )

    def _candidates(self):
        candidates = []
        for symbol, history in self.histories.items():
            if len(history) < self.scanner_config.min_candles:
                continue
            if self.continuous_authorized:
                snapshot = classify_regime(history, self.regime_filter)
                if not regime_allows_strategy(self.strategy_name, snapshot):
                    self.state.regime_filtered_count += 1
                    continue
                if self.strategy.generate_signal(history).action != Action.BUY:
                    continue
            scan = evaluate_symbol(symbol, Market.CRYPTO, history, self.scanner_config, model=self.model)
            if scan.rejected:
                self.state.rejected_opportunities_count += 1
                continue
            candidates.append(scan)
        candidates.sort(
            key=lambda result: (
                result.combined_opportunity_score
                if result.combined_opportunity_score is not None
                else result.opportunity_score
            ),
            reverse=True,
        )
        return candidates

    def _execute_pending_entry(self) -> tuple[bool, str]:
        pending = self.state.pending_entry
        if not pending:
            return False, ""
        symbol = str(pending["symbol"])
        history = self.histories.get(symbol, [])
        if not history:
            return False, "Waiting for a candle for the queued entry."
        signal_time = datetime.fromisoformat(str(pending["signal_time"]))
        newer = [candle for candle in history if candle.timestamp > signal_time]
        if not newer:
            return False, "Waiting for the next newly observed candle before entry."
        signal_history = [candle for candle in history if candle.timestamp <= signal_time]
        scan = evaluate_symbol(symbol, Market.CRYPTO, signal_history, self.scanner_config, model=self.model)
        candle = newer[-1]
        entered, reason = self._enter(scan, candle, candle.close)
        self.state.pending_entry = None
        return entered, (f"Deferred signal executed on a newly observed candle. {reason}" if entered else reason)

    def _enter(self, scan, candle: Candle, price: float) -> tuple[bool, str]:
        signal = Signal(Action.BUY, scan.rank_score / 100.0, scan.explanation, scan.confidence, scan.risk_score / 100.0)
        decision = self.risk.evaluate(Market.CRYPTO, self.state.cash, scan.symbol, signal, candle, entry_price=price)
        if not decision.approved:
            self.state.rejected_opportunities_count += 1
            return False, decision.reason
        self.state.cash -= price * decision.quantity
        self.state.open_position = {
            "symbol": scan.symbol,
            "quantity": decision.quantity,
            "entry_price": price,
            "stop_loss": decision.stop_loss,
            "target": decision.target,
            "entry_time": candle.timestamp.isoformat(),
            "entry_loop": self.state.loops_completed,
            "entry_reason": self._entry_reason(scan),
            "highest_completed_high": price,
            "exit_streak": 0,
        }
        return True, self.state.open_position["entry_reason"]

    def _entry_reason(self, scan) -> str:
        return (
            f"{scan.explanation} strategy={self.strategy_name} "
            f"ml_probability={scan.ml_probability} ml_score={scan.ml_score}"
        )

    def _update_histories(self) -> None:
        for symbol in self.symbols:
            try:
                fetched = self.provider.fetch_symbol(symbol, interval=self.interval, days=self.lookback_candles)
                merged = {candle.timestamp: candle for candle in [*self.histories.get(symbol, []), *fetched]}
                self.histories[symbol] = sorted(merged.values(), key=lambda candle: candle.timestamp)[-self.lookback_candles :]
                if self.histories[symbol]:
                    self.state.last_processed_timestamp[symbol] = self.histories[symbol][-1].timestamp.isoformat()
            except Exception as exc:
                self.state.errors.append(f"{symbol}: {exc}")

    def _exit_decision(self) -> tuple[float, str]:
        position = self.state.open_position
        if not position:
            return 0.0, ""
        symbol = str(position["symbol"])
        history = self.histories.get(symbol, [])
        if not history:
            return 0.0, ""
        candle = history[-1]
        entry = float(position["entry_price"])
        highest = float(position.get("highest_completed_high", entry))
        if highest >= entry * (1.0 + self.breakeven_trigger_pct):
            position["stop_loss"] = max(
                float(position["stop_loss"]),
                entry,
                highest * (1.0 - self.trailing_stop_pct),
            )
        if candle.low <= float(position["stop_loss"]):
            return float(position["stop_loss"]), "Stop loss hit"
        if candle.high >= float(position["target"]):
            return float(position["target"]), "Target hit"

        held = self.state.loops_completed - int(position.get("entry_loop", 0))
        if held >= self.max_holding_loops:
            return candle.close, "Max holding loops reached"
        exit_condition = False
        exit_reason = ""
        if self.continuous_authorized and held >= self.min_holding_loops:
            snapshot = classify_regime(history, self.regime_filter)
            if not regime_allows_strategy(self.strategy_name, snapshot):
                exit_condition, exit_reason = True, f"Confirmed regime exit: {snapshot.name}"
            if self.strategy.generate_signal(history).action == Action.SELL:
                exit_condition, exit_reason = True, "Confirmed strategy sell"
            scan = evaluate_symbol(symbol, Market.CRYPTO, history, self.scanner_config, model=self.model)
            if scan.rejected or scan.risk_score >= 85.0:
                exit_condition = True
                exit_reason = f"Confirmed scanner-risk exit: {scan.rejection_reason or 'dangerous risk score'}"
        streak = int(position.get("exit_streak", 0))
        position["exit_streak"] = streak + 1 if exit_condition else 0
        position["highest_completed_high"] = max(highest, candle.high)
        if int(position["exit_streak"]) >= self.exit_confirmation_loops:
            return candle.close, exit_reason or "Confirmed exit condition"
        return 0.0, ""

    def _close_position(self, exit_price: float, timestamp: str, exit_reason: str) -> None:
        position = self.state.open_position
        if not position:
            return
        quantity = float(position["quantity"])
        entry_price = float(position["entry_price"])
        gross = (exit_price - entry_price) * quantity
        costs = self.costs.estimate(Market.CRYPTO, entry_price, exit_price, quantity)
        tax_result = self.tax.estimate(Market.CRYPTO, gross, exit_value=exit_price * quantity)
        tax = float(tax_result["tax"])
        tds = float(tax_result["tds_cashflow"])
        net = gross - costs["fees"] - costs["slippage"] - tax
        held = self.state.loops_completed - int(position.get("entry_loop", 0))
        self.state.cash += entry_price * quantity + net
        self.state.trade_history.append(
            asdict(
                LivePaperTrade(
                    position["symbol"],
                    position["entry_time"],
                    timestamp,
                    entry_price,
                    exit_price,
                    quantity,
                    gross,
                    costs["fees"],
                    costs["slippage"],
                    tax,
                    net,
                    position.get("entry_reason", ""),
                    exit_reason,
                    tds,
                    held,
                )
            )
        )
        self.state.open_position = None
        self.state.last_exit_loop = self.state.loops_completed

    def _equity(self) -> float:
        if not self.state.open_position:
            return self.state.cash
        position = self.state.open_position
        history = self.histories.get(position["symbol"], [])
        price = history[-1].close if history else float(position["entry_price"])
        return self.state.cash + float(position["quantity"]) * price

    def _latest_time(self) -> datetime | None:
        times = [history[-1].timestamp for history in self.histories.values() if history]
        return max(times) if times else None

    def _load_state(self) -> LivePaperState:
        if not self.state_path.exists():
            state = LivePaperState(
                cash=self.initial_cash,
                warnings=["PAPER MODE ONLY: no live trading or order endpoints."],
            )
            self._write_state(state)
            return state
        try:
            return LivePaperState(**json.loads(self.state_path.read_text(encoding="utf-8")))
        except (OSError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid paper-live state file {self.state_path}: {exc}") from exc

    def _save_state(self) -> None:
        self._write_state(self.state)

    def _write_state(self, state: LivePaperState) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
        temporary.replace(self.state_path)
