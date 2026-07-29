from __future__ import annotations

from datetime import datetime, timedelta

from tradebot.models import Action, Candle
from tradebot.strategies.compression_breakout import CompressionBreakoutRetestStrategy
from tradebot.strategies.multi_timeframe_trend import MultiTimeframeTrendStrategy
from tradebot.strategies.relative_strength import CrossAssetRelativeStrengthStrategy


def candle(index: int, close: float, *, open_price: float | None = None, high: float | None = None, low: float | None = None, volume: float = 10_000) -> Candle:
    open_value = close if open_price is None else open_price
    return Candle(
        datetime(2025, 1, 1) + timedelta(days=index),
        open_value,
        max(open_value, close) * 1.002 if high is None else high,
        min(open_value, close) * 0.998 if low is None else low,
        close,
        volume,
    )


def history_from_closes(closes: list[float], *, volume: float = 10_000) -> list[Candle]:
    return [
        candle(
            index,
            close,
            open_price=closes[index - 1] if index else close,
            volume=volume,
        )
        for index, close in enumerate(closes)
    ]


def test_multi_timeframe_trend_buys_completed_pullback_recovery():
    strategy = MultiTimeframeTrendStrategy(
        fast_window=3,
        medium_window=5,
        slow_window=8,
        slope_window=3,
        min_slow_slope=0.0,
        pullback_tolerance=0.02,
        breakout_lookback=3,
        continuation_buffer=0.0,
        volume_multiplier=0.8,
        max_extension=0.20,
    )
    candles = history_from_closes(
        [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 109, 113]
    )

    signal = strategy.generate_signal(candles)

    assert signal.action == Action.BUY
    assert "Multi-timeframe" in signal.reason
    assert "pullback" in signal.reason or "continuation" in signal.reason


def test_multi_timeframe_trend_does_not_buy_failed_slow_trend():
    strategy = MultiTimeframeTrendStrategy(
        fast_window=3,
        medium_window=5,
        slow_window=8,
        slope_window=3,
        min_slow_slope=0.0,
        breakout_lookback=3,
        volume_multiplier=0.8,
    )
    candles = history_from_closes([120, 119, 118, 117, 116, 115, 114, 113, 112, 111, 110, 109, 108, 107])

    assert strategy.generate_signal(candles).action != Action.BUY


def compression_history() -> list[Candle]:
    candles = [
        candle(index, 100.0 + (0.08 if index % 2 else -0.08), open_price=100.0, high=100.25, low=99.75)
        for index in range(15)
    ]
    candles.append(candle(15, 103.0, open_price=100.0, high=103.3, low=99.9, volume=20_000))
    candles.append(candle(16, 102.4, open_price=101.5, high=102.8, low=100.15, volume=12_000))
    return candles


def compression_strategy(*, allow_continuation: bool) -> CompressionBreakoutRetestStrategy:
    return CompressionBreakoutRetestStrategy(
        compression_window=6,
        atr_short_window=2,
        atr_long_window=6,
        max_atr_ratio=1.10,
        max_range_pct=0.05,
        breakout_buffer=0.0,
        retest_bars=2,
        retest_tolerance=0.02,
        volume_multiplier=0.8,
        allow_continuation=allow_continuation,
        max_extension=0.08,
    )


def test_compression_breakout_waits_for_completed_retest():
    strategy = compression_strategy(allow_continuation=False)
    candles = compression_history()

    breakout_only = strategy.generate_signal(candles[:-1])
    retested = strategy.generate_signal(candles)

    assert breakout_only.action != Action.BUY
    assert retested.action == Action.BUY
    assert "retest" in retested.reason.lower()


def test_compression_continuation_requires_post_breakout_candle():
    strategy = compression_strategy(allow_continuation=True)
    candles = compression_history()[:-1]
    post_breakout = candle(
        16,
        103.5,
        open_price=103.0,
        high=104.0,
        low=102.5,
        volume=12_000,
    )

    breakout_only = strategy.generate_signal(candles)
    continued = strategy.generate_signal([*candles, post_breakout])

    assert breakout_only.action != Action.BUY
    assert continued.action == Action.BUY
    assert "continuation" in continued.reason.lower()


def test_relative_strength_ignores_peer_candles_after_signal_time():
    target = history_from_closes([100, 102, 104, 106, 108, 112, 116])
    peer = history_from_closes([100, 101, 102, 103, 104, 105, 106])
    future = Candle(
        peer[-1].timestamp + timedelta(days=1),
        106,
        510,
        105,
        500,
        50_000,
    )
    strategy = CrossAssetRelativeStrengthStrategy(
        "BTCUSDT",
        {"BTCUSDT": target, "ETHUSDT": [*peer, future]},
        lookback=5,
        short_lookback=2,
        top_n=1,
        exit_rank=2,
        min_return=0.0,
        min_breadth=0.0,
        volatility_penalty=0.0,
    )

    signal = strategy.generate_signal(target)

    assert signal.action == Action.BUY
    assert "rank 1/2" in signal.reason


def test_relative_strength_blocks_lower_ranked_asset():
    leader = history_from_closes([100, 102, 104, 106, 108, 112, 116])
    laggard = history_from_closes([100, 100.5, 101, 101.5, 102, 102.5, 103])
    strategy = CrossAssetRelativeStrengthStrategy(
        "ETHUSDT",
        {"BTCUSDT": leader, "ETHUSDT": laggard},
        lookback=5,
        short_lookback=2,
        top_n=1,
        exit_rank=2,
        min_return=0.0,
        min_breadth=0.0,
        volatility_penalty=0.0,
    )

    assert strategy.generate_signal(laggard).action != Action.BUY
