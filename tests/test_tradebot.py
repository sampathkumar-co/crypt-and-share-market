from tradebot.data.csv_loader import load_candles
from tradebot.models import Action, Market, Signal
from tradebot.strategies.momentum import MomentumVolumeStrategy
from tradebot.risk.risk_manager import RiskConfig, RiskManager
from tradebot.risk.cost_engine import CostEngine
from tradebot.risk.tax_engine import TaxEngine
from tradebot.backtest.paper_trader import PaperTrader
from tradebot.backtest.walk_forward import split_windows, walk_forward

DATA='data/samples/crypto_btcusdt.csv'

def test_csv_loading():
    candles=load_candles(DATA)
    assert len(candles)==60
    assert candles[0].close > 0

def test_strategy_signal_generation():
    signal=MomentumVolumeStrategy().generate_signal(load_candles(DATA)[:16])
    assert signal.action in {Action.BUY, Action.HOLD, Action.SELL}
    assert 0 <= signal.confidence <= 1

def test_risk_manager_rejection_low_volume():
    candle=load_candles(DATA)[0]
    signal=Signal(Action.BUY, .9, 'test', .9, .2)
    decision=RiskManager(RiskConfig(min_volume=999999999)).evaluate(Market.CRYPTO, 100000, 'BTCUSDT', signal, candle)
    assert not decision.approved
    assert 'low volume' in decision.reason.lower()

def test_cost_calculation():
    costs=CostEngine().estimate(Market.EQUITY, 100, 110, 10)
    assert costs['total_cost'] > 0
    assert costs['break_even_price'] > 100

def test_tax_calculation():
    tax=TaxEngine().estimate(Market.CRYPTO, 1000)
    assert tax['tax'] == 300

def test_paper_backtest_result():
    result=PaperTrader(Market.CRYPTO, MomentumVolumeStrategy()).run('BTCUSDT', load_candles(DATA))
    assert result.starting_cash == 100000
    assert isinstance(result.rejected_signals, list)
    assert result.ending_cash > 0

def test_walk_forward_split_logic():
    candles=load_candles(DATA)
    windows=split_windows(candles, 20, 10)
    assert len(windows) == 4
    result=walk_forward('BTCUSDT', Market.CRYPTO, candles, MomentumVolumeStrategy(), 20, 10)
    assert 0 <= result.stability_score <= 1
    assert result.windows[0]['selected_parameters']
