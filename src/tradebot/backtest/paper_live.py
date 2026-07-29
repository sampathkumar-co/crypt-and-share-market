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
    """Forward paper simulation using public/read-only candles only.

    Continuous mode is blocked unless a fresh historical research-gate report
    explicitly approves the selected strategy. It never places real orders.
    """

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
        use_regime_filter: bool = True,
        regime_filter: RegimeFilterConfig | None = None,
        defer_entries: bool = False,
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
        if cooldown_loops < 0:
            raise ValueError("cooldown_loops cannot be negative")
        if exit_confirmation_loops < 1:
            raise ValueError("exit_confirmation_loops must be positive")
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
        self.use_regime_filter = use_regime_filter
        self.regime_filter = regime_filter or RegimeFilterConfig()
        self.defer_entries = defer_entries

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
        for loop_index in range(max_loops):
            summary = self.run_once()
            print(summary)
            if loop_index < max_loops - 1 and sleep_seconds > 0:
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
            raise ValueError("continuous mode requires a positive sleep_seconds value")
        gate = validate_forward_gate(
            gate_report_path,
            strategy_name=self.strategy_name,
            market=Market.CRYPTO,
            max_age_days=gate_max_age_days,
        )
        self.defer_entries = True
        self.state.warnings.append(
            f"Continuous forward paper mode authorized by gate "
            f"{gate.get('dataset_fingerprint', '')[:12]} for strategy={self.strategy_name}."
        )
        self._save_state()
        print(
            "CONTINUOUS PAPER MODE ONLY - historical gates passed; "
            "this is still not live trading or proof of profit."
        )
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
            exit_price, exit_reason = self._exit_decision(latest_time)
            if exit_reason:
                self._close_position(exit_price, timestamp, exit_reason)
                action = "exit"
                reason = exit_reason
            else:
                action = "hold"
                reason = "Open paper position remains active."

        if not self.state.open_position and self.state.pending_entry:
            executed, pending_reason = self._try_execute_pending()
            if executed:
                action = "enter"
                reason = pending_reason
            elif pending_reason:
                reason = pending_reason

        if (
            not self.state.open_position
            and not self.state.pending_entry
            and self.state.loops_completed - self.state.last_exit_loop > self.cooldown_loops
        ):
            candidates = []
            for symbol, candles in self.histories.items():
                if len(candles) < self.scanner_config.min_candles:
                    continue

                if self.use_regime_filter:
                    snapshot = classify_regime(candles, self.regime_filter)
                    if not regime_allows_strategy(self.strategy_name, snapshot):
                        self.state.regime_filtered_count += 1
                        continue

                strategy_signal = self.strategy.generate_signal(candles)
                if strategy_signal.action != Action.BUY:
                    continue

                scan = evaluate_symbol(
                    symbol,
                    Market.CRYPTO,
                    candles,
                    self.scanner_config,
                    model=self.model,
                )
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
            if candidates:
                top = candidates[0]
                top_score = (
                    top.combined_opportunity_score
                    if top.combined_opportunity_score is not None
                    else top.opportunity_score
                )
                top_candidate = f"{top.symbol} score={top_score:.1f}"
                candle = self.histories[top.symbol][-1]
                if self.defer_entries:
                    self.state.pending_entry = {
                        "symbol": top.symbol,
                        "signal_time": candle.timestamp.isoformat(),
                        "entry_reason": (
                            f"{top.explanation} strategy={self.strategy_name} "
                            f"ml_probability={top.ml_probability} ml_score={top.ml_score}"
                        ),
                        "risk_score": top.risk_score,
                        "confidence": top.confidence,
                        "rank_score": top.rank_score,
                    }
                    action = "queue"
                    reason = "Signal queued for the next newly observed candle."
                else:
                    entered, enter_reason = self._enter_at_price(
                        top,
                        candle,
                        candle.close,
                    )
                    if entered:
                        action = "enter"
                    reason = enter_reason

        equity = self._equity()
        self.state.equity_history.append(
            {"timestamp": timestamp, "equity": equity, "cash": self.state.cash}
        )
        self.state.loops_completed += 1
        self._save_state()
        open_position = self.state.open_position["symbol"] if self.state.open_position else "-"
        return (
            f"{timestamp} cash={self.state.cash:.2f} equity={equity:.2f} open={open_position} "
            f"top={top_candidate} action={action} reason={reason} "
            f"warnings={'; '.join(self.state.warnings) or '-'}"
        )

    def _try_execute_pending(self) -> tuple[bool, str]:
        pending = self.state.pending_entry
        if not pending:
            return False, ""
        symbol = str(pending["symbol"])
        candles = self.histories.get(symbol, [])
        if not candles:
            return False, "Waiting for a candle for the queued entry."
        signal_time = datetime.fromisoformat(str(pending["signal_time"]))
        newer = [candle for candle in candles if candle.timestamp > signal_time]
        if not newer:
            return False, "Waiting for the next completed candle before entry."

        candle = newer[0]
        scan = evaluate_symbol(
            symbol,
            Market.CRYPTO,
            [item for item in candles if item.timestamp <= signal_time],
            self.scanner_config,
            model=self.model,
        )
        entered, reason = self._enter_at_price(scan, candle, candle.close)
        self.state.pending_entry = None
        if entered:
            reason = f"Deferred signal executed at newly observed candle close. {reason}"
        return entered, reason

    def _enter_at_price(self, scan, candle: Candle, entry_price: float) -> tuple[bool, str]:
        risk_signal = Signal(
            Action.BUY,
            scan.rank_score / 100.0,
            scan.explanation,
            scan.confidence,
            scan.risk_score / 100.0,
        )
        decision = self.risk.evaluate(
            Market.CRYPTO,
            self.state.cash,
            scan.symbol,
            risk_signal,
            candle,
            entry_price=entry_price,
        )
        if not decision.approved:
            self.state.rejected_opportunities_count += 1
            return False, decision.reason

        cost = entry_price * decision.quantity
        self.state.cash -= cost
        self.state.open_position = {
            "symbol": scan.symbol,
            "quantity": decision.quantity,
            "entry_price": entry_price,
            "stop_loss": decision.stop_loss,
            "target": decision.target,
            "entry_time": candle.timestamp.isoformat(),
            "entry_loop": self.state.loops_completed,
            "entry_reason": (
                f"{scan.explanation} strategy={self.strategy_name} "
                f"ml_probability={scan.ml_probability} ml_score={scan.ml_score}"
            ),
            "highest_completed_high": entry_price,
            "exit_streak": 0,
        }
        return True, self.state.open_position["entry_reason"]

    def _update_histories(self) -> None:
        for symbol in self.symbols:
            try:
                candles = self.provider.fetch_symbol(
                    symbol,
                    interval=self.interval,
                    days=self.lookback_candles,
                )
                merged = {
                    candle.timestamp: candle
                    for candle in [*self.histories.get(symbol, []), *candles]
                }
                self.histories[symbol] = sorted(
                    merged.values(),
                    key=lambda candle: candle.timestamp,
                )[-self.lookback_candles :]
                if self.histories[symbol]:
                    self.state.last_processed_timestamp[symbol] = (
                        self.histories[symbol][-1].timestamp.isoformat()
                    )
            except Exception as exc:
                self.state.errors.append(f"{symbol}: {exc}")

    def _exit_decision(self, latest_time: datetime | None) -> tuple[float, str]:
        if not self.state.open_position or latest_time is None:
            return 0.0, ""
        position = self.state.open_position
        symbol = position["symbol"]
        candle = self.histories.get(symbol, [])[-1] if self.histories.get(symbol) else None
        if candle is None:
            return 0.0, ""

        highest = float(position.get("highest_completed_high", position["entry_price"]))
        if highest >= float(position["entry_price"]) * (1.0 + self.breakeven_trigger_pct):
            trailing = highest * (1.0 - self.trailing_stop_pct)
            position["stop_loss"] = max(
                float(position["stop_loss"]),
                float(position["entry_price"]),
                trailing,
            )

        if candle.low <= float(position["stop_loss"]):
            return float(position["stop_loss"]), "Stop loss hit"
        if candle.high >= float(position["target"]):
            return float(position["target"]), "Target hit"

        loops_held = self.state.loops_completed - int(position.get("entry_loop", 0))
        if loops_held >= self.max_holding_loops:
            return candle.close, "Max holding loops reached"

        exit_condition = False
        exit_reason = ""
        if loops_held >= self.min_holding_loops:
            history = self.histories[symbol]
            if self.use_regime_filter:
                snapshot = classify_regime(history, self.regime_filter)
                if not regime_allows_strategy(self.strategy_name, snapshot):
                    exit_condition = True
                    exit_reason = f"Confirmed regime exit: {snapshot.name}"
            strategy_signal = self.strategy.generate_signal(history)
            if strategy_signal.action == Action.SELL:
                exit_condition = True
                exit_reason = "Confirmed strategy sell"
            scan = evaluate_symbol(
                symbol,
                Market.CRYPTO,
                history,
                self.scanner_config,
                model=self.model,
            )
            if scan.rejected or scan.risk_score >= 85.0:
                exit_condition = True
                exit_reason = (
                    f"Confirmed scanner-risk exit: "
                    f"{scan.rejection_reason or 'dangerous risk score'}"
                )

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
        tax_result = self.tax.estimate(
            Market.CRYPTO,
            gross,
            exit_value=exit_price * quantity,
        )
        tax = float(tax_result["tax"])
        tds = float(tax_result["tds_cashflow"])
        net = gross - costs["fees"] - costs["slippage"] - tax
        holding_loops = self.state.loops_completed - int(position.get("entry_loop", 0))
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
                    holding_loops,
                )
            )
        )
        self.state.open_position = None
        self.state.last_exit_loop = self.state.loops_completed

    def _equity(self) -> float:
        if not self.state.open_position:
            return self.state.cash
        position = self.state.open_position
        candles = self.histories.get(position["symbol"], [])
        price = candles[-1].close if candles else float(position["entry_price"])
        return self.state.cash + float(position["quantity"]) * price

    def _latest_time(self) -> datetime | None:
        times = [candles[-1].timestamp for candles in self.histories.values() if candles]
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
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            return LivePaperState(**payload)
        except (OSError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid paper-live state file {self.state_path}: {exc}") from exc

    def _save_state(self) -> None:
        self._write_state(self.state)

    def _write_state(self, state: LivePaperState) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
        temporary.replace(self.state_path)
