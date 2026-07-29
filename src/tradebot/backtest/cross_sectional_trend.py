from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any

from tradebot.backtest.metrics import max_drawdown
from tradebot.backtest.research_gate import dataset_fingerprint, load_histories
from tradebot.models import Candle, Market
from tradebot.risk.cost_engine import CostEngine
from tradebot.risk.tax_engine import TaxEngine


SCHEMA_VERSION = "1.0"
REQUIRED_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "ADAUSDT")


@dataclass(frozen=True)
class TrendVariant:
    name: str
    rebalance_bars: int
    top_n: int
    min_cash_reserve: float
    max_asset_weight: float


VARIANTS = (
    TrendVariant("primary", rebalance_bars=14, top_n=2, min_cash_reserve=0.20, max_asset_weight=0.40),
    TrendVariant("slow", rebalance_bars=21, top_n=2, min_cash_reserve=0.25, max_asset_weight=0.375),
    TrendVariant("diversified", rebalance_bars=14, top_n=3, min_cash_reserve=0.20, max_asset_weight=0.35),
)


@dataclass(frozen=True)
class CrossSectionalTrendConfig:
    history_bars: int = 1800
    observed_holdout_bars: int = 1000
    validation_bars: int = 800
    warmup_bars: int = 180
    test_bars: int = 60
    validation_periods: int = 10
    embargo_bars: int = 20
    fast_lookback: int = 30
    slow_lookback: int = 90
    trend_window: int = 120
    volatility_window: int = 30
    min_market_breadth: float = 0.60
    min_trade_weight: float = 0.05
    extra_cost_per_turnover: float = 0.001
    min_active_periods: int = 6
    min_positive_period_fraction: float = 0.60
    min_beat_buy_hold_fraction: float = 0.50
    min_positive_variants: int = 2
    min_leave_one_out_positive_fraction: float = 0.80
    max_portfolio_drawdown: float = 0.10
    starting_cash: float = 100000.0

    def __post_init__(self) -> None:
        if self.history_bars != self.observed_holdout_bars + self.validation_bars:
            raise ValueError("history split must equal validation plus observed holdout")
        used = self.warmup_bars + self.validation_periods * self.test_bars + self.embargo_bars
        if used != self.validation_bars:
            raise ValueError("validation split must preserve warmup, ten tests and embargo")
        if self.trend_window > self.warmup_bars or self.slow_lookback > self.warmup_bars:
            raise ValueError("warmup is too short for frozen features")
        if not 0 < self.min_market_breadth <= 1:
            raise ValueError("min_market_breadth must be between zero and one")
        if not 0 <= self.min_trade_weight < 1:
            raise ValueError("min_trade_weight must be between zero and one")
        if self.starting_cash <= 0:
            raise ValueError("starting_cash must be positive")


@dataclass
class TrendPeriod:
    variant: str
    period: int
    test_start: str
    test_end: str
    net_return: float
    stressed_return: float
    buy_and_hold_return: float
    btc_buy_and_hold_return: float
    excess_vs_buy_and_hold: float
    max_drawdown: float
    turnover: float
    transactions: int
    active: bool
    average_cash_weight: float
    regime_cash_rebalances: int
    selected_symbols: list[str]
    total_fees: float
    total_slippage: float
    total_tax: float


@dataclass
class VariantSummary:
    variant: str
    periods: list[TrendPeriod]
    active_periods: int
    average_return: float
    median_return: float
    compounded_return: float
    positive_fraction: float
    average_stressed_return: float
    average_buy_and_hold_return: float
    average_excess_vs_buy_and_hold: float
    beat_buy_and_hold_fraction: float
    worst_drawdown: float
    average_turnover: float


@dataclass
class CrossSectionalTrendReport:
    schema_version: str
    generated_at: str
    market: str
    symbols: list[str]
    full_dataset_fingerprint: str
    validation_dataset_fingerprint: str
    validation_start: str
    validation_end: str
    embargo_start: str
    embargo_end: str
    observed_holdout_start: str
    observed_holdout_end: str
    config: dict[str, Any]
    variants: list[VariantSummary]
    leave_one_out_average_returns: dict[str, float]
    leave_one_out_positive_fraction: float
    accepted: bool
    eligible_for_forward_paper: bool
    reasons: list[str]
    paper_only: bool = True
    authorizes_real_trading: bool = False


@dataclass
class _Position:
    quantity: float
    average_price: float
    entry_time: datetime


@dataclass
class _Simulation:
    net_return: float
    stressed_return: float
    buy_and_hold_return: float
    btc_buy_and_hold_return: float
    max_drawdown: float
    turnover: float
    transactions: int
    active: bool
    average_cash_weight: float
    regime_cash_rebalances: int
    selected_symbols: list[str]
    total_fees: float
    total_slippage: float
    total_tax: float


def _intersection_histories(
    folder: str | Path,
    config: CrossSectionalTrendConfig,
) -> dict[str, list[Candle]]:
    loaded = load_histories(folder)
    missing = sorted(set(REQUIRED_SYMBOLS) - set(loaded))
    if missing:
        raise ValueError(f"Missing required histories: {', '.join(missing)}")
    timestamps = [set(candle.timestamp for candle in loaded[symbol]) for symbol in REQUIRED_SYMBOLS]
    common = sorted(set.intersection(*timestamps))
    if len(common) < config.history_bars:
        raise ValueError(
            f"Only {len(common)} aligned candles are available; {config.history_bars} are required"
        )
    selected_times = common[-config.history_bars :]
    selected_set = set(selected_times)
    aligned: dict[str, list[Candle]] = {}
    for symbol in REQUIRED_SYMBOLS:
        mapping = {candle.timestamp: candle for candle in loaded[symbol] if candle.timestamp in selected_set}
        aligned[symbol] = [mapping[timestamp] for timestamp in selected_times]
        if len(aligned[symbol]) != config.history_bars:
            raise ValueError(f"{symbol} is incomplete after timestamp alignment")
    return aligned


def _validation_histories(
    histories: dict[str, list[Candle]],
    config: CrossSectionalTrendConfig,
) -> dict[str, list[Candle]]:
    return {symbol: candles[: config.validation_bars] for symbol, candles in histories.items()}


def _daily_volatility(candles: list[Candle], window: int) -> float:
    closes = [candle.close for candle in candles[-(window + 1) :]]
    returns = [closes[index] / closes[index - 1] - 1.0 for index in range(1, len(closes)) if closes[index - 1] > 0]
    if len(returns) < 2:
        return 0.0
    return pstdev(returns) * math.sqrt(365.0)


def _features(candles: list[Candle], config: CrossSectionalTrendConfig) -> dict[str, float | bool]:
    close = candles[-1].close
    fast_base = candles[-(config.fast_lookback + 1)].close
    slow_base = candles[-(config.slow_lookback + 1)].close
    fast_return = close / fast_base - 1.0 if fast_base > 0 else 0.0
    slow_return = close / slow_base - 1.0 if slow_base > 0 else 0.0
    trend_average = mean(candle.close for candle in candles[-config.trend_window :])
    volatility = _daily_volatility(candles, config.volatility_window)
    trend_ok = close > trend_average and fast_return > 0 and slow_return > 0
    score = (0.40 * fast_return + 0.60 * slow_return) / max(volatility, 0.10)
    return {
        "fast_return": fast_return,
        "slow_return": slow_return,
        "volatility": volatility,
        "trend_ok": trend_ok,
        "score": score,
    }


def _capped_inverse_volatility_weights(
    selected: list[tuple[str, dict[str, float | bool]]],
    exposure: float,
    cap: float,
) -> dict[str, float]:
    if not selected or exposure <= 0:
        return {}
    remaining = exposure
    pending = {symbol: 1.0 / max(float(features["volatility"]), 0.05) for symbol, features in selected}
    weights: dict[str, float] = {}
    while pending and remaining > 1e-12:
        denominator = sum(pending.values())
        capped: list[str] = []
        for symbol, raw in pending.items():
            proposed = remaining * raw / denominator
            if proposed >= cap - 1e-12:
                weights[symbol] = cap
                capped.append(symbol)
        if not capped:
            for symbol, raw in pending.items():
                weights[symbol] = remaining * raw / denominator
            remaining = 0.0
            break
        for symbol in capped:
            remaining -= weights[symbol]
            pending.pop(symbol)
    return {symbol: max(0.0, value) for symbol, value in weights.items()}


def _target_weights(
    prior: dict[str, list[Candle]],
    variant: TrendVariant,
    config: CrossSectionalTrendConfig,
) -> tuple[dict[str, float], bool]:
    features = {symbol: _features(candles, config) for symbol, candles in prior.items()}
    trend_count = sum(bool(item["trend_ok"]) for item in features.values())
    breadth = trend_count / len(features) if features else 0.0
    slow_returns = [float(item["slow_return"]) for item in features.values()]
    btc_ok = bool(features.get("BTCUSDT", {}).get("trend_ok", False)) if "BTCUSDT" in features else True
    healthy = breadth >= config.min_market_breadth and median(slow_returns) > 0 and btc_ok
    if not healthy:
        return {}, True
    eligible = [
        (symbol, item)
        for symbol, item in features.items()
        if bool(item["trend_ok"]) and float(item["score"]) > 0
    ]
    eligible.sort(key=lambda row: (float(row[1]["score"]), row[0]), reverse=True)
    selected = eligible[: variant.top_n]
    exposure = 1.0 - variant.min_cash_reserve
    return _capped_inverse_volatility_weights(selected, exposure, variant.max_asset_weight), False


def _benchmark_return(
    histories: dict[str, list[Candle]],
    start_index: int,
    end_index: int,
) -> tuple[float, float]:
    returns = []
    for candles in histories.values():
        first = candles[start_index].open
        last = candles[end_index].close
        returns.append(last / first - 1.0 if first > 0 else 0.0)
    btc = histories.get("BTCUSDT")
    btc_return = 0.0
    if btc:
        first = btc[start_index].open
        btc_return = btc[end_index].close / first - 1.0 if first > 0 else 0.0
    return mean(returns) if returns else 0.0, btc_return


def _simulate_period(
    histories: dict[str, list[Candle]],
    period: int,
    variant: TrendVariant,
    config: CrossSectionalTrendConfig,
) -> TrendPeriod:
    start_index = config.warmup_bars + (period - 1) * config.test_bars
    end_index = start_index + config.test_bars - 1
    cash = config.starting_cash
    positions: dict[str, _Position] = {}
    costs = CostEngine()
    taxes = TaxEngine()
    side_rate = costs.config.crypto_exchange_fee_pct + costs.config.crypto_slippage_pct
    total_fees = 0.0
    total_slippage = 0.0
    total_tax = 0.0
    total_turnover = 0.0
    gross_pnl = 0.0
    transactions = 0
    regime_cash_rebalances = 0
    selected_symbols: set[str] = set()
    cash_weights: list[float] = []
    curve = [cash]

    def mark(index: int, field: str = "close") -> float:
        value = cash
        for symbol, position in positions.items():
            price = getattr(histories[symbol][index], field)
            value += position.quantity * price
        return value

    def sell(symbol: str, quantity: float, price: float, timestamp: datetime) -> None:
        nonlocal cash, total_fees, total_slippage, total_tax, total_turnover, gross_pnl, transactions
        position = positions[symbol]
        quantity = min(quantity, position.quantity)
        if quantity <= 1e-12:
            return
        notional = quantity * price
        fee = notional * costs.config.crypto_exchange_fee_pct
        slippage = notional * costs.config.crypto_slippage_pct
        gross = (price - position.average_price) * quantity
        holding_days = max(0, (timestamp.date() - position.entry_time.date()).days)
        tax = float(taxes.estimate(Market.CRYPTO, gross, holding_days, exit_value=notional)["tax"])
        cash += notional - fee - slippage - tax
        total_fees += fee
        total_slippage += slippage
        total_tax += tax
        total_turnover += notional
        gross_pnl += gross
        transactions += 1
        remaining = position.quantity - quantity
        if remaining <= 1e-10:
            positions.pop(symbol)
        else:
            position.quantity = remaining

    def buy(symbol: str, notional: float, price: float, timestamp: datetime) -> None:
        nonlocal cash, total_fees, total_slippage, total_turnover, transactions
        if notional <= 0 or price <= 0:
            return
        affordable = cash / (1.0 + side_rate)
        notional = min(notional, affordable)
        if notional <= 1e-8:
            return
        fee = notional * costs.config.crypto_exchange_fee_pct
        slippage = notional * costs.config.crypto_slippage_pct
        quantity = notional / price
        cash -= notional + fee + slippage
        total_fees += fee
        total_slippage += slippage
        total_turnover += notional
        transactions += 1
        current = positions.get(symbol)
        if current is None:
            positions[symbol] = _Position(quantity, price, timestamp)
        else:
            combined = current.quantity + quantity
            current.average_price = (
                current.average_price * current.quantity + price * quantity
            ) / combined
            current.quantity = combined

    for offset, index in enumerate(range(start_index, end_index + 1)):
        timestamp = histories[next(iter(histories))][index].timestamp
        if offset % variant.rebalance_bars == 0:
            prior = {symbol: candles[:index] for symbol, candles in histories.items()}
            target_weights, regime_cash = _target_weights(prior, variant, config)
            if regime_cash:
                regime_cash_rebalances += 1
            selected_symbols.update(target_weights)
            equity_open = mark(index, "open")
            current_values = {
                symbol: position.quantity * histories[symbol][index].open
                for symbol, position in positions.items()
            }
            all_symbols = set(current_values) | set(target_weights)
            for symbol in sorted(all_symbols):
                target = equity_open * target_weights.get(symbol, 0.0)
                current_value = current_values.get(symbol, 0.0)
                difference = current_value - target
                if difference > config.min_trade_weight * equity_open and symbol in positions:
                    sell(
                        symbol,
                        difference / histories[symbol][index].open,
                        histories[symbol][index].open,
                        timestamp,
                    )
            equity_after_sales = mark(index, "open")
            for symbol in sorted(target_weights, key=lambda item: target_weights[item], reverse=True):
                target = equity_after_sales * target_weights[symbol]
                current_value = positions.get(symbol).quantity * histories[symbol][index].open if symbol in positions else 0.0
                difference = target - current_value
                if difference > config.min_trade_weight * equity_after_sales:
                    buy(symbol, difference, histories[symbol][index].open, timestamp)
        equity_close = mark(index, "close")
        curve.append(equity_close)
        cash_weights.append(cash / equity_close if equity_close > 0 else 1.0)

    final_timestamp = histories[next(iter(histories))][end_index].timestamp
    for symbol in list(positions):
        sell(symbol, positions[symbol].quantity, histories[symbol][end_index].close, final_timestamp)
    curve.append(cash)
    buy_hold, btc_buy_hold = _benchmark_return(histories, start_index, end_index)
    net_return = cash / config.starting_cash - 1.0
    turnover = total_turnover / config.starting_cash
    stressed = net_return - turnover * config.extra_cost_per_turnover
    return TrendPeriod(
        variant=variant.name,
        period=period,
        test_start=histories[next(iter(histories))][start_index].timestamp.isoformat(),
        test_end=histories[next(iter(histories))][end_index].timestamp.isoformat(),
        net_return=net_return,
        stressed_return=stressed,
        buy_and_hold_return=buy_hold,
        btc_buy_and_hold_return=btc_buy_hold,
        excess_vs_buy_and_hold=net_return - buy_hold,
        max_drawdown=max_drawdown(curve),
        turnover=turnover,
        transactions=transactions,
        active=transactions > 0,
        average_cash_weight=mean(cash_weights) if cash_weights else 1.0,
        regime_cash_rebalances=regime_cash_rebalances,
        selected_symbols=sorted(selected_symbols),
        total_fees=total_fees,
        total_slippage=total_slippage,
        total_tax=total_tax,
    )


def _summarize(variant: TrendVariant, periods: list[TrendPeriod]) -> VariantSummary:
    returns = [period.net_return for period in periods]
    compounded = math.prod(1.0 + value for value in returns) - 1.0 if returns else 0.0
    return VariantSummary(
        variant=variant.name,
        periods=periods,
        active_periods=sum(period.active for period in periods),
        average_return=mean(returns) if returns else 0.0,
        median_return=median(returns) if returns else 0.0,
        compounded_return=compounded,
        positive_fraction=sum(value > 0 for value in returns) / len(returns) if returns else 0.0,
        average_stressed_return=mean(period.stressed_return for period in periods) if periods else 0.0,
        average_buy_and_hold_return=mean(period.buy_and_hold_return for period in periods) if periods else 0.0,
        average_excess_vs_buy_and_hold=mean(period.excess_vs_buy_and_hold for period in periods) if periods else 0.0,
        beat_buy_and_hold_fraction=(
            sum(period.net_return > period.buy_and_hold_return for period in periods) / len(periods)
            if periods
            else 0.0
        ),
        worst_drawdown=max((period.max_drawdown for period in periods), default=0.0),
        average_turnover=mean(period.turnover for period in periods) if periods else 0.0,
    )


def _variant_result(
    histories: dict[str, list[Candle]],
    variant: TrendVariant,
    config: CrossSectionalTrendConfig,
) -> VariantSummary:
    periods = [
        _simulate_period(histories, period, variant, config)
        for period in range(1, config.validation_periods + 1)
    ]
    return _summarize(variant, periods)


def evaluate_cross_sectional_trend(
    folder: str | Path,
    market: Market = Market.CRYPTO,
    config: CrossSectionalTrendConfig | None = None,
) -> CrossSectionalTrendReport:
    if market != Market.CRYPTO:
        raise ValueError("v0.8 validation is frozen to crypto")
    config = config or CrossSectionalTrendConfig()
    full = _intersection_histories(folder, config)
    validation = _validation_histories(full, config)
    summaries = [_variant_result(validation, variant, config) for variant in VARIANTS]
    primary_variant = VARIANTS[0]
    leave_one_out: dict[str, float] = {}
    for omitted in REQUIRED_SYMBOLS:
        subset = {symbol: candles for symbol, candles in validation.items() if symbol != omitted}
        leave_one_out[omitted] = _variant_result(subset, primary_variant, config).average_return
    leave_positive = sum(value > 0 for value in leave_one_out.values()) / len(leave_one_out)
    primary = summaries[0]
    positive_variants = sum(
        summary.average_return > 0
        and summary.median_return > 0
        and summary.compounded_return > 0
        for summary in summaries
    )
    reasons: list[str] = []
    if len(primary.periods) != config.validation_periods:
        reasons.append("incomplete_validation_periods")
    if primary.active_periods < config.min_active_periods:
        reasons.append("too_few_active_periods")
    if primary.average_return <= 0:
        reasons.append("average_validation_return_not_positive")
    if primary.median_return <= 0:
        reasons.append("median_validation_return_not_positive")
    if primary.compounded_return <= 0:
        reasons.append("compounded_validation_return_not_positive")
    if primary.positive_fraction < config.min_positive_period_fraction:
        reasons.append("too_few_profitable_periods")
    if primary.average_stressed_return <= 0:
        reasons.append("extra_cost_stressed_return_not_positive")
    if primary.average_excess_vs_buy_and_hold <= 0:
        reasons.append("did_not_beat_equal_weight_buy_and_hold_on_average")
    if primary.beat_buy_and_hold_fraction < config.min_beat_buy_hold_fraction:
        reasons.append("did_not_beat_buy_and_hold_often_enough")
    if primary.worst_drawdown > config.max_portfolio_drawdown:
        reasons.append("portfolio_drawdown_too_high")
    if positive_variants < config.min_positive_variants:
        reasons.append("insufficient_positive_fixed_variants")
    if leave_positive < config.min_leave_one_out_positive_fraction:
        reasons.append("leave_one_asset_out_results_not_robust")
    accepted = not reasons
    first_symbol = REQUIRED_SYMBOLS[0]
    validation_end_index = config.validation_bars - config.embargo_bars - 1
    embargo_start_index = validation_end_index + 1
    observed_start_index = config.validation_bars
    return CrossSectionalTrendReport(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        market=market.value,
        symbols=list(REQUIRED_SYMBOLS),
        full_dataset_fingerprint=dataset_fingerprint(full),
        validation_dataset_fingerprint=dataset_fingerprint(validation),
        validation_start=validation[first_symbol][0].timestamp.isoformat(),
        validation_end=validation[first_symbol][validation_end_index].timestamp.isoformat(),
        embargo_start=validation[first_symbol][embargo_start_index].timestamp.isoformat(),
        embargo_end=validation[first_symbol][-1].timestamp.isoformat(),
        observed_holdout_start=full[first_symbol][observed_start_index].timestamp.isoformat(),
        observed_holdout_end=full[first_symbol][-1].timestamp.isoformat(),
        config=asdict(config),
        variants=summaries,
        leave_one_out_average_returns=leave_one_out,
        leave_one_out_positive_fraction=leave_positive,
        accepted=accepted,
        eligible_for_forward_paper=accepted,
        reasons=reasons or ["Frozen cross-sectional trend portfolio passed all validation gates."],
    )


def write_report(path: str | Path, report: CrossSectionalTrendReport) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    temporary.replace(output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the frozen v0.8 shared-cash cross-sectional trend portfolio.")
    parser.add_argument("--folder", required=True)
    parser.add_argument("--market", choices=[item.value for item in Market], default=Market.CRYPTO.value)
    parser.add_argument("--json-out", default="reports/cross-sectional-trend/cross_sectional_trend.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = evaluate_cross_sectional_trend(args.folder, market=Market(args.market))
    write_report(args.json_out, report)
    print(json.dumps(asdict(report), indent=2))
    return 0 if report.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
