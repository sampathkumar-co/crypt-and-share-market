from tradebot.models import Action, Candle, Signal
from tradebot.strategies.base import Strategy, avg

class BreakoutStrategy(Strategy):
    name = "breakout"
    def __init__(self, lookback: int = 10, buffer: float = 0.002):
        self.lookback = lookback; self.buffer = buffer
    def generate_signal(self, candles: list[Candle]) -> Signal:
        if len(candles) <= self.lookback:
            return Signal(Action.HOLD, 0, "Not enough candles", 0, 0.5)
        window = candles[-self.lookback-1:-1]
        last = candles[-1]
        high = max(c.high for c in window); low = min(c.low for c in window)
        volume_strength = last.volume / max(avg([c.volume for c in window]), 1)
        risk = min(1, (high - low) / max(last.close, 1) * 4)
        if last.close > high * (1 + self.buffer):
            score = min(1, (last.close / high - 1) * 30 + volume_strength * 0.15)
            return Signal(Action.BUY, score, f"Close broke above {self.lookback}-bar high", min(.9, score), risk)
        if last.close < low * (1 - self.buffer):
            return Signal(Action.SELL, .7, f"Close broke below {self.lookback}-bar low", .7, risk)
        return Signal(Action.HOLD, .2, "No breakout", .4, risk)
