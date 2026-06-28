from pathlib import Path
from tradebot.data.csv_loader import load_candles
from tradebot.models import Market, ScanResult
from tradebot.risk.cost_engine import CostEngine
from tradebot.strategies.momentum import MomentumVolumeStrategy
from tradebot.strategies.base import avg

def scan_crypto_folder(folder: str | Path) -> list[ScanResult]:
    return _scan(folder, Market.CRYPTO)

def _scan(folder: str | Path, market: Market) -> list[ScanResult]:
    out=[]; strategy=MomentumVolumeStrategy(); costs=CostEngine()
    for path in Path(folder).glob("*.csv"):
        candles=load_candles(path); signal=strategy.generate_signal(candles); recent=candles[-10:]
        vol_strength=recent[-1].volume/max(avg([c.volume for c in recent[:-1]]),1); trend=(recent[-1].close-recent[0].close)/recent[0].close
        volatility=(max(c.high for c in recent)-min(c.low for c in recent))/recent[-1].close; liquidity=min(1, recent[-1].volume/10000)
        possible=max(0, signal.score*.04 - costs.estimate(market, recent[-1].close, recent[-1].close*1.04, 1)["total_cost"]/recent[-1].close)
        rank=signal.score*.4+vol_strength*.15+max(0,trend)*2+liquidity*.2-volatility*.2+possible
        out.append(ScanResult(path.stem, market, signal, vol_strength, trend, volatility, liquidity, possible, rank))
    return sorted(out, key=lambda r: r.rank_score, reverse=True)
