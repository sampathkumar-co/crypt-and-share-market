from __future__ import annotations
from abc import ABC, abstractmethod
from tradebot.models import Candle, Signal

class Strategy(ABC):
    name = "base"
    @abstractmethod
    def generate_signal(self, candles: list[Candle]) -> Signal: ...

def pct_change(old: float, new: float) -> float:
    return (new - old) / old if old else 0.0

def avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
