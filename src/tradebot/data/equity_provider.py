from pathlib import Path
from tradebot.data.csv_loader import load_candles
from tradebot.models import Candle

class EquityCSVProvider:
    """Paper-only CSV provider; never connects to broker trading APIs."""
    def get_history(self, path: str | Path) -> list[Candle]:
        return load_candles(path)
