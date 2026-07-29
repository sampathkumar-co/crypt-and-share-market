from __future__ import annotations

from tradebot.backtest.sparse_trend_replication import (
    OLDER_V08_REFERENCE,
    SparseReplicationConfig,
)


def test_secondary_split_has_thirteen_periods_and_embargo():
    config = SparseReplicationConfig()
    assert config.holdout_bars == 1000
    assert config.warmup_bars == 180
    assert config.replication_periods == 13
    assert config.warmup_bars + config.replication_periods * config.test_bars + config.embargo_bars == 1000


def test_method_parameters_match_v08_defaults():
    config = SparseReplicationConfig()
    assert config.fast_lookback == 30
    assert config.slow_lookback == 90
    assert config.trend_window == 120
    assert config.volatility_window == 30
    assert config.min_market_breadth == 0.60
    assert config.min_trade_weight == 0.05
    assert config.extra_cost_per_turnover == 0.001


def test_older_reference_is_positive_but_not_accepted():
    assert OLDER_V08_REFERENCE["average_return"] > 0
    assert OLDER_V08_REFERENCE["compounded_return"] > 0
    assert OLDER_V08_REFERENCE["average_stressed_return"] > 0
    assert OLDER_V08_REFERENCE["accepted"] is False
