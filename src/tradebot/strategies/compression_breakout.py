from __future__ import annotations

from statistics import pstdev

from tradebot.models import Action, Candle, Signal
from tradebot.strategies.base import Strategy, avg


def _true_ranges(candles: list[Candle]) -> list[float]:
    ranges: list[float] = []
    for index, candle in enumerate(candles):
        previous_close = candles[index - 1].close if index else candle.open
        ranges.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        )
    return ranges


class CompressionBreakoutRetestStrategy(Strategy):
    """Long-only compression breakout followed by retest or continuation.

    The breakout level is formed strictly from candles before the breakout
    candle. A retest must also be visible on a completed candle before a BUY
    signal can be emitted.
    """

    name = "compression_breakout_retest"

    def __init__(
        self,
        compression_window: int = 20,
        atr_short_window: int = 5,
        atr_long_window: int = 20,
        max_atr_ratio: float = 0.75,
        max_range_pct: float = 0.12,
        breakout_buffer: float = 0.002,
        retest_bars: int = 3,
        retest_tolerance: float = 0.015,
        volume_multiplier: float = 1.0,
        allow_continuation: bool = True,
        max_extension: float = 0.05,
    ) -> None:
        if compression_window < 5:
            raise ValueError("compression_window must be at least 5")
        if atr_short_window < 2 or atr_long_window <= atr_short_window:
            raise ValueError("Require 2 <= atr_short_window < atr_long_window")
        if retest_bars < 1:
            raise ValueError("retest_bars must be positive")
        if not 0 < max_atr_ratio <= 1.5:
            raise ValueError("max_atr_ratio must be between 0 and 1.5")
        if max_range_pct <= 0 or breakout_buffer < 0 or retest_tolerance < 0:
            raise ValueError("range and breakout thresholds are invalid")
        if volume_multiplier <= 0 or max_extension <= 0:
            raise ValueError("volume_multiplier and max_extension must be positive")
        self.compression_window = compression_window
        self.atr_short_window = atr_short_window
        self.atr_long_window = atr_long_window
        self.max_atr_ratio = max_atr_ratio
        self.max_range_pct = max_range_pct
        self.breakout_buffer = breakout_buffer
        self.retest_bars = retest_bars
        self.retest_tolerance = retest_tolerance
        self.volume_multiplier = volume_multiplier
        self.allow_continuation = allow_continuation
        self.max_extension = max_extension

    @property
    def required_history(self) -> int:
        return self.compression_window + self.retest_bars + self.atr_long_window + 2

    def generate_signal(self, candles: list[Candle]) -> Signal:
        if len(candles) < self.required_history:
            return Signal(Action.HOLD, 0.0, "Not enough completed candles for compression setup", 0.0, 0.5)

        breakout = self._latest_completed_breakout(candles)
        last = candles[-1]
        recent_mid = avg([candle.close for candle in candles[-self.compression_window :]])
        recent_returns = [
            candles[index].close / max(candles[index - 1].close, 1e-12) - 1.0
            for index in range(len(candles) - min(20, len(candles) - 1), len(candles))
        ]
        volatility = pstdev(recent_returns) if len(recent_returns) >= 2 else 0.0

        if breakout is not None:
            breakout_index, level, breakout_close, atr_ratio, range_pct, volume_strength = breakout
            bars_since_breakout = len(candles) - 1 - breakout_index
            extension = last.close / max(level, 1e-12) - 1.0
            retest = (
                bars_since_breakout >= 1
                and last.low <= level * (1.0 + self.retest_tolerance)
                and last.close >= level
                and last.close >= last.open
            )
            continuation = (
                self.allow_continuation
                and bars_since_breakout <= self.retest_bars
                and last.close >= breakout_close
                and extension <= self.max_extension
            )
            score = min(
                1.0,
                max(
                    0.0,
                    (1.0 - min(atr_ratio, 1.0)) * 0.35
                    + (1.0 - min(range_pct / max(self.max_range_pct, 1e-12), 1.0)) * 0.25
                    + min(volume_strength / 2.0, 1.0) * 0.20
                    + (0.20 if retest else 0.10 if continuation else 0.0),
                ),
            )
            risk = min(1.0, volatility * 12.0 + max(extension, 0.0) * 5.0)
            if (retest or continuation) and extension <= self.max_extension:
                setup = "retest" if retest else "continuation"
                return Signal(
                    Action.BUY,
                    score,
                    (
                        f"Compression breakout {setup}: level={level:.6g}, "
                        f"ATR ratio={atr_ratio:.2f}, range={range_pct:.2%}, "
                        f"volume={volume_strength:.2f}x"
                    ),
                    min(0.95, 0.55 + score * 0.40),
                    risk,
                )

            if last.close < level * (1.0 - self.retest_tolerance):
                return Signal(
                    Action.SELL,
                    min(1.0, abs(extension) * 12.0 + 0.30),
                    "Compression breakout failed below its completed range high",
                    0.75,
                    min(1.0, volatility * 15.0),
                )

        if last.close < recent_mid and candles[-2].close < recent_mid:
            return Signal(
                Action.SELL,
                0.55,
                "Price lost the compression-window mean",
                0.60,
                min(1.0, volatility * 15.0),
            )

        return Signal(
            Action.HOLD,
            0.15,
            "No completed compression breakout and valid retest/continuation",
            0.40,
            min(1.0, volatility * 12.0),
        )

    def _latest_completed_breakout(
        self,
        candles: list[Candle],
    ) -> tuple[int, float, float, float, float, float] | None:
        first_index = max(
            self.compression_window + self.atr_long_window,
            len(candles) - self.retest_bars - 1,
        )
        for breakout_index in range(len(candles) - 1, first_index - 1, -1):
            pre_start = breakout_index - self.compression_window
            if pre_start < 1:
                continue
            pre = candles[pre_start:breakout_index]
            breakout_candle = candles[breakout_index]
            level = max(candle.high for candle in pre)
            floor = min(candle.low for candle in pre)
            mean_close = avg([candle.close for candle in pre])
            range_pct = (level - floor) / max(mean_close, 1e-12)

            atr_source_start = max(0, breakout_index - self.atr_long_window - 1)
            atr_source = candles[atr_source_start:breakout_index]
            true_ranges = _true_ranges(atr_source)
            if len(true_ranges) < self.atr_long_window:
                continue
            short_atr = avg(true_ranges[-self.atr_short_window :])
            long_atr = avg(true_ranges[-self.atr_long_window :])
            atr_ratio = short_atr / max(long_atr, 1e-12)
            average_volume = avg([candle.volume for candle in pre])
            volume_strength = breakout_candle.volume / max(average_volume, 1.0)
            compressed = atr_ratio <= self.max_atr_ratio and range_pct <= self.max_range_pct
            broke_out = breakout_candle.close > level * (1.0 + self.breakout_buffer)
            if compressed and broke_out and volume_strength >= self.volume_multiplier:
                return (
                    breakout_index,
                    level,
                    breakout_candle.close,
                    atr_ratio,
                    range_pct,
                    volume_strength,
                )
        return None
