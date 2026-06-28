from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

class Market(str, Enum):
    CRYPTO = "crypto"
    EQUITY = "equity"

class Action(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"

@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass(frozen=True)
class Signal:
    action: Action
    score: float
    reason: str
    confidence: float
    risk_score: float

@dataclass
class Position:
    symbol: str
    quantity: float
    entry_price: float
    stop_loss: float
    target: float
    entry_time: datetime

@dataclass
class Trade:
    symbol: str
    market: Market
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    fees: float
    slippage_cost: float
    tax_estimate: float
    net_pnl: float
    pnl_percent: float
    reason: str

@dataclass
class BacktestResult:
    starting_cash: float
    ending_cash: float
    gross_return: float
    net_return: float
    win_rate: float
    max_drawdown: float
    total_fees: float
    total_tax: float
    trades: list[Trade] = field(default_factory=list)
    rejected_signals: list[str] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    average_win: float = 0.0
    average_loss: float = 0.0
    risk_warnings: list[str] = field(default_factory=list)

@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    quantity: float = 0.0
    stop_loss: float = 0.0
    target: float = 0.0
    reason: str = ""
    warnings: tuple[str, ...] = ()

@dataclass(frozen=True)
class ScanResult:
    symbol: str
    market: Market
    signal: Signal
    volume_strength: float
    trend_strength: float
    volatility_risk: float
    liquidity_safety: float
    net_profit_possibility: float
    rank_score: float
    rank: int = 0
    opportunity_score: float = 0.0
    risk_score: float = 0.0
    confidence: float = 0.0
    expected_move_percent: float = 0.0
    estimated_net_profit_after_cost_tax: float = 0.0
    rejected: bool = False
    rejection_reason: str = ""
    explanation: str = ""
    breakout_quality: float = 0.0
    pullback_quality: float = 0.0
    ml_probability: float | None = None
    ml_score: float | None = None
    combined_opportunity_score: float | None = None
    ml_explanation: str = ""

@dataclass(frozen=True)
class WalkForwardResult:
    windows: list[dict[str, Any]]
    stability_score: float
    accepted: bool
    reason: str
