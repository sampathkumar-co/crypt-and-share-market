from tradebot.backtest.walk_forward import (
    WalkForwardConfig,
    parameter_grid,
    rejection_reasons,
    select_best_parameters,
    walk_forward,
)
from tradebot.data.csv_loader import load_candles
from tradebot.models import Market

DATA = "data/samples/crypto_btcusdt.csv"


def test_parameter_grid_generation():
    grid = parameter_grid({"lookback": [3, 5], "min_return": [0.01, 0.02]})
    assert grid == [
        {"lookback": 3, "min_return": 0.01},
        {"lookback": 3, "min_return": 0.02},
        {"lookback": 5, "min_return": 0.01},
        {"lookback": 5, "min_return": 0.02},
    ]


def test_train_selection_returns_best_candidate_with_metrics():
    candles = load_candles(DATA)[:30]
    selection = select_best_parameters(
        "BTCUSDT",
        Market.CRYPTO,
        candles,
        "momentum",
        parameter_grid({"lookback": [3, 5], "min_return": [0.008], "volume_multiplier": [1.0]}),
        WalkForwardConfig(train_size=30, test_size=15),
    )
    assert selection["selected"]["params"] in [candidate["params"] for candidate in selection["candidates"]]
    assert "net_return" in selection["selected"]["metrics"]
    assert "selection_score" in selection["selected"]


def test_walk_forward_evaluates_only_selected_params_on_unseen_test():
    result = walk_forward(
        "BTCUSDT",
        Market.CRYPTO,
        load_candles(DATA),
        strategy_name="momentum",
        parameter_grids={"momentum": {"lookback": [3, 5], "min_return": [0.008], "volume_multiplier": [1.0]}},
        config=WalkForwardConfig(train_size=30, test_size=15, min_trades=0),
    )
    assert result.windows
    first = result.windows[0]
    assert first["train_start"] != first["test_start"]
    assert first["selected_parameters"] in [candidate["params"] for candidate in first["train_candidates"]]
    assert "test_metrics" in first


def test_rejection_rules_detect_overfit_and_drawdown():
    reasons = rejection_reasons(
        {"trades": 3, "max_drawdown": 0.05, "net_return": 0.20, "win_rate": 1.0},
        {"trades": 0, "max_drawdown": 0.40, "net_return": -0.10, "win_rate": 0.0},
        WalkForwardConfig(min_trades=1, max_drawdown=0.25, overfit_gap_limit=0.05),
    )
    assert "too_few_test_trades" in reasons
    assert "high_test_drawdown" in reasons
    assert "train_test_overfit_gap" in reasons
    assert "profitable_train_failed_unseen_test" in reasons
