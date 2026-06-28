from tradebot.models import Action, Candle, Signal
from tradebot.strategies.base import Strategy, avg

class MeanReversionStrategy(Strategy):
    name = "mean_reversion"
    def __init__(self, lookback: int = 10, threshold: float = 0.025):
        self.lookback = lookback; self.threshold = threshold
    def generate_signal(self, candles: list[Candle]) -> Signal:
        if len(candles) <= self.lookback:
            return Signal(Action.HOLD, 0, "Not enough candles", 0, 0.5)
        closes = [c.close for c in candles[-self.lookback:]]
        mean = avg(closes); last = candles[-1].close
        deviation = (last - mean) / mean if mean else 0
        risk = min(1, abs(deviation) * 10)
        if deviation < -self.threshold:
            return Signal(Action.BUY, min(1, abs(deviation) * 12), f"Price {deviation:.2%} below mean", .65, risk)
        if deviation > self.threshold:
            return Signal(Action.SELL, min(1, deviation * 12), f"Price {deviation:.2%} above mean", .65, risk)
        return Signal(Action.HOLD, .2, "Near mean", .4, risk)
