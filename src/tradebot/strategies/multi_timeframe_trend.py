from __future__ import annotations

from tradebot.models import Action, Candle, Signal
from tradebot.strategies.base import Strategy, avg


class MultiTimeframeTrendStrategy(Strategy):
    """Long-only trend strategy with pullback-recovery or continuation entries.

    All calculations use completed candles supplied to ``generate_signal``. The
    backtester remains responsible for filling an accepted signal at the next
    bar open.
    """

    name = "multi_timeframe_trend"

    def __init__(
        self,
        fast_window: int = 8,
        medium_window: int = 21,
        slow_window: int = 63,
        slope_window: int = 10,
        min_slow_slope: float = 0.005,
        pullback_tolerance: float = 0.015,
        breakout_lookback: int = 10,
        continuation_buffer: float = 0.002,
        volume_multiplier: float = 1.0,
        max_extension: float = 0.08,
    ) -> None:
        if fast_window < 2 or not fast_window < medium_window < slow_window:
            raise ValueError("Require 2 <= fast_window < medium_window < slow_window")
        if slope_window < 2 or breakout_lookback < 2:
            raise ValueError("slope_window and breakout_lookback must be at least 2")
        if min_slow_slope < 0 or pullback_tolerance < 0 or continuation_buffer < 0:
            raise ValueError("trend thresholds cannot be negative")
        if volume_multiplier <= 0 or max_extension <= 0:
            raise ValueError("volume_multiplier and max_extension must be positive")
        self.fast_window = fast_window
        self.medium_window = medium_window
        self.slow_window = slow_window
        self.slope_window = slope_window
        self.min_slow_slope = min_slow_slope
        self.pullback_tolerance = pullback_tolerance
        self.breakout_lookback = breakout_lookback
        self.continuation_buffer = continuation_buffer
        self.volume_multiplier = volume_multiplier
        self.max_extension = max_extension

    @property
    def required_history(self) -> int:
        return max(
            self.slow_window + self.slope_window,
            self.breakout_lookback + 2,
            self.medium_window + 2,
        )

    def generate_signal(self, candles: list[Candle]) -> Signal:
        if len(candles) < self.required_history:
            return Signal(Action.HOLD, 0.0, "Not enough completed candles for multi-timeframe trend", 0.0, 0.5)

        closes = [candle.close for candle in candles]
        last = candles[-1]
        previous = candles[-2]
        fast = avg(closes[-self.fast_window :])
        prior_fast = avg(closes[-self.fast_window - 1 : -1])
        medium = avg(closes[-self.medium_window :])
        slow = avg(closes[-self.slow_window :])
        prior_slow = avg(
            closes[-self.slow_window - self.slope_window : -self.slope_window]
        )
        slow_slope = slow / max(prior_slow, 1e-12) - 1.0
        volume_average = avg(
            [candle.volume for candle in candles[-self.medium_window - 1 : -1]]
        )
        volume_strength = last.volume / max(volume_average, 1.0)
        continuation_level = max(
            candle.high for candle in candles[-self.breakout_lookback - 1 : -1]
        )
        extension = last.close / max(medium, 1e-12) - 1.0

        aligned_trend = (
            fast > medium > slow
            and slow_slope >= self.min_slow_slope
            and last.close > slow
        )
        pullback_recovery = (
            previous.close <= prior_fast * (1.0 + self.pullback_tolerance)
            and last.close > fast
            and last.close > previous.close
        )
        continuation = last.close > continuation_level * (1.0 + self.continuation_buffer)
        volume_ok = volume_strength >= self.volume_multiplier

        trend_gap = fast / max(slow, 1e-12) - 1.0
        score = min(
            1.0,
            max(
                0.0,
                trend_gap * 8.0
                + slow_slope * 12.0
                + max(volume_strength - 0.8, 0.0) * 0.20
                + (0.15 if pullback_recovery else 0.0)
                + (0.15 if continuation else 0.0),
            ),
        )
        risk = min(1.0, max(0.0, extension / max(self.max_extension, 1e-12)))

        if (
            aligned_trend
            and volume_ok
            and extension <= self.max_extension
            and (pullback_recovery or continuation)
        ):
            setup = "pullback recovery" if pullback_recovery else "continuation breakout"
            return Signal(
                Action.BUY,
                score,
                (
                    f"Multi-timeframe {setup}: fast>medium>slow, "
                    f"slow_slope={slow_slope:.2%}, volume={volume_strength:.2f}x"
                ),
                min(0.95, 0.55 + score * 0.40),
                risk,
            )

        if last.close < medium or fast < medium or slow_slope < -self.min_slow_slope:
            return Signal(
                Action.SELL,
                min(1.0, abs(min(extension, slow_slope)) * 10.0 + 0.25),
                "Multi-timeframe trend structure failed",
                0.70,
                min(1.0, abs(slow_slope) * 15.0),
            )

        return Signal(
            Action.HOLD,
            score,
            "Trend exists but no completed pullback-recovery or continuation entry",
            0.45,
            risk,
        )
