from __future__ import annotations

from statistics import pstdev

from tradebot.models import Action, Candle, Signal
from tradebot.strategies.base import Strategy


class CrossAssetRelativeStrengthStrategy(Strategy):
    """Rank one symbol against peer assets using completed candles only.

    The strategy is evaluated as an independent long-or-cash sleeve for each
    symbol. It never allocates capital across sleeves and therefore does not
    change portfolio or meta-selection logic.
    """

    name = "cross_asset_relative_strength"

    def __init__(
        self,
        symbol: str,
        peer_histories: dict[str, list[Candle]],
        lookback: int = 40,
        short_lookback: int = 10,
        top_n: int = 2,
        exit_rank: int = 3,
        min_return: float = 0.02,
        min_breadth: float = 0.40,
        volatility_penalty: float = 0.25,
    ) -> None:
        if lookback < 5 or short_lookback < 2 or short_lookback >= lookback:
            raise ValueError("Require lookback >= 5 and 2 <= short_lookback < lookback")
        if top_n < 1 or exit_rank < top_n:
            raise ValueError("top_n must be positive and exit_rank must be >= top_n")
        if not 0 <= min_breadth <= 1:
            raise ValueError("min_breadth must be between 0 and 1")
        if volatility_penalty < 0:
            raise ValueError("volatility_penalty cannot be negative")
        normalized = {
            peer.upper(): sorted(history, key=lambda candle: candle.timestamp)
            for peer, history in peer_histories.items()
            if history
        }
        self.symbol = symbol.upper()
        if self.symbol not in normalized:
            raise ValueError(f"Missing target history for {self.symbol}")
        if len(normalized) < 2:
            raise ValueError("Relative-strength strategy requires at least two assets")
        self.peer_histories = normalized
        self.lookback = lookback
        self.short_lookback = short_lookback
        self.top_n = top_n
        self.exit_rank = exit_rank
        self.min_return = min_return
        self.min_breadth = min_breadth
        self.volatility_penalty = volatility_penalty

    @property
    def required_history(self) -> int:
        return self.lookback + 1

    def generate_signal(self, candles: list[Candle]) -> Signal:
        if len(candles) < self.required_history:
            return Signal(Action.HOLD, 0.0, "Not enough completed candles for relative strength", 0.0, 0.5)

        timestamp = candles[-1].timestamp
        rows: list[tuple[str, float, float, float]] = []
        for symbol, history in self.peer_histories.items():
            available = [candle for candle in history if candle.timestamp <= timestamp]
            if len(available) < self.required_history:
                return Signal(
                    Action.HOLD,
                    0.0,
                    f"Peer {symbol} lacks completed relative-strength history",
                    0.0,
                    0.5,
                )
            closes = [candle.close for candle in available]
            long_return = closes[-1] / max(closes[-self.lookback - 1], 1e-12) - 1.0
            short_return = closes[-1] / max(closes[-self.short_lookback - 1], 1e-12) - 1.0
            daily_returns = [
                closes[index] / max(closes[index - 1], 1e-12) - 1.0
                for index in range(len(closes) - self.lookback, len(closes))
            ]
            volatility = pstdev(daily_returns) if len(daily_returns) >= 2 else 0.0
            score = long_return * 0.65 + short_return * 0.35 - volatility * self.volatility_penalty
            rows.append((symbol, score, long_return, volatility))

        rows.sort(key=lambda item: (-item[1], item[0]))
        rank_by_symbol = {symbol: index for index, (symbol, *_rest) in enumerate(rows, start=1)}
        target_row = next(row for row in rows if row[0] == self.symbol)
        rank = rank_by_symbol[self.symbol]
        score_value = target_row[1]
        long_return = target_row[2]
        volatility = target_row[3]
        breadth = sum(row[2] > 0 for row in rows) / len(rows)
        normalized_score = min(1.0, max(0.0, 0.5 + score_value * 5.0))
        risk = min(1.0, volatility * 15.0 + max(-long_return, 0.0) * 4.0)

        if rank <= self.top_n and long_return >= self.min_return and breadth >= self.min_breadth:
            return Signal(
                Action.BUY,
                normalized_score,
                (
                    f"Relative-strength rank {rank}/{len(rows)} with "
                    f"return={long_return:.2%}, breadth={breadth:.0%}"
                ),
                min(0.95, 0.55 + normalized_score * 0.40),
                risk,
            )

        if rank > self.exit_rank or long_return <= 0:
            return Signal(
                Action.SELL,
                min(1.0, 0.30 + max(rank - self.exit_rank, 0) * 0.15 + max(-long_return, 0.0) * 5.0),
                (
                    f"Relative-strength rank deteriorated to {rank}/{len(rows)} "
                    f"with return={long_return:.2%}"
                ),
                0.70,
                risk,
            )

        return Signal(
            Action.HOLD,
            normalized_score,
            (
                f"Relative-strength rank {rank}/{len(rows)} did not meet entry threshold; "
                f"breadth={breadth:.0%}"
            ),
            0.45,
            risk,
        )
