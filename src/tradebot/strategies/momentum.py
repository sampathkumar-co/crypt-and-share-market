from tradebot.models import Action, Candle, Signal
from tradebot.strategies.base import Strategy, avg, pct_change

class MomentumVolumeStrategy(Strategy):
    name = "momentum_volume"
    def __init__(self, lookback: int = 5, min_return: float = 0.015, volume_multiplier: float = 1.15):
        self.lookback = lookback; self.min_return = min_return; self.volume_multiplier = volume_multiplier
    def generate_signal(self, candles: list[Candle]) -> Signal:
        if len(candles) <= self.lookback:
            return Signal(Action.HOLD, 0, "Not enough candles", 0, 0.5)
        recent = candles[-self.lookback:]
        ret = pct_change(recent[0].close, recent[-1].close)
        vol_strength = recent[-1].volume / max(avg([c.volume for c in recent[:-1]]), 1)
        risk = min(1.0, abs(ret) * 8)
        score = max(0.0, min(1.0, ret * 12 + (vol_strength - 1) * 0.4))
        if ret > self.min_return and vol_strength >= self.volume_multiplier:
            return Signal(Action.BUY, score, f"Momentum {ret:.2%} with volume {vol_strength:.2f}x", min(0.95, score), risk)
        if ret < -self.min_return:
            return Signal(Action.SELL, min(1, abs(ret) * 10), f"Negative momentum {ret:.2%}", 0.65, risk)
        return Signal(Action.HOLD, score, "Momentum/volume below threshold", 0.45, risk)
