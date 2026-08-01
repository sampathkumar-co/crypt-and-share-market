from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np

ASSETS = ("BTC", "ETH", "SOL", "XRP", "ADA")
PRODUCTS = {asset: f"{asset}-USD" for asset in ASSETS}
BASE_URL = "https://api.exchange.coinbase.com"
GRANULARITY = 86400
CANDLE_COUNT = 1000
WARMUP = 200
HORIZONS = (1, 3, 7)
STANDARD_ONE_WAY_COST = 0.001
STRESS_ONE_WAY_COST = 0.002
PROTOCOL_PATH = Path("research/V41_DAILY_MULTI_HORIZON_LEARNED_PROTOCOL.md")
CONTRACT_PATH = Path("research/V411_DAILY_IMPLEMENTATION_CONTRACT.md")
SCHEMA_VERSION = "4.1-daily-multihorizon"


class LearnedDailyV41Error(RuntimeError):
    pass


@dataclass(frozen=True)
class Bar:
    date: datetime
    low: float
    high: float
    open: float
    close: float
    volume: float


@dataclass
class Dataset:
    X: np.ndarray
    returns: dict[int, np.ndarray]
    opportunities: dict[int, np.ndarray]
    downside3: np.ndarray
    regimes: np.ndarray
    dates: list[datetime]
    assets: list[str]
    feature_names: list[str]
    quote_volume_30: np.ndarray


@dataclass
class Bundle:
    return_models: dict[int, list[Any]]
    opportunity_models: dict[int, list[Any]]
    downside_models: list[Any]
    regime_model: Any
    config: dict[str, Any]
    opportunity_threshold: float
    required_positive_horizons: int
    uncertainty_threshold: float
    liquidity_threshold: float
    feature_names: list[str]


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def completed_day(now: datetime) -> datetime:
    now = now.astimezone(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)


def candle_url(product: str, start: datetime, end: datetime) -> str:
    query = urlencode({
        "granularity": str(GRANULARITY),
        "start": utc_iso(start),
        "end": utc_iso(end + timedelta(days=1)),
    })
    return f"{BASE_URL}/products/{product}/candles?{query}"


def download_json(url: str, timeout: float = 25.0, attempts: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers={
                "User-Agent": "tradebot-v41-paper-research/1.0",
                "Accept": "application/json",
            })
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                if response.status != 200:
                    raise LearnedDailyV41Error(f"HTTP {response.status}")
                content = response.read()
            if not content:
                raise LearnedDailyV41Error("empty Coinbase response")
            return content
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
    raise LearnedDailyV41Error(f"Coinbase download failed: {last}")


def parse_candles(content: bytes, start: datetime, end: datetime) -> dict[datetime, Bar]:
    payload = json.loads(content.decode("utf-8"))
    if not isinstance(payload, list):
        raise LearnedDailyV41Error("candle response is not a list")
    bars: dict[datetime, Bar] = {}
    for row in payload:
        if not isinstance(row, list) or len(row) < 6:
            raise LearnedDailyV41Error("invalid Coinbase candle row")
        stamp = datetime.fromtimestamp(int(row[0]), tz=timezone.utc)
        if not start <= stamp <= end:
            continue
        low, high, open_, close, volume = map(float, row[1:6])
        if min(low, high, open_, close) <= 0.0 or volume < 0.0:
            raise LearnedDailyV41Error("nonpositive candle value")
        candidate = Bar(stamp, low, high, open_, close, volume)
        if stamp in bars and bars[stamp] != candidate:
            raise LearnedDailyV41Error("conflicting duplicate candle")
        bars[stamp] = candidate
    return bars


def fetch_history(
    end: datetime,
    downloader: Callable[[str], bytes] = download_json,
) -> tuple[dict[str, dict[datetime, Bar]], list[dict[str, Any]]]:
    start = end - timedelta(days=CANDLE_COUNT - 1)
    result = {asset: {} for asset in ASSETS}
    inventory: list[dict[str, Any]] = []
    chunk_days = 249
    for asset in ASSETS:
        cursor = start
        index = 0
        while cursor <= end:
            finish = min(end, cursor + timedelta(days=chunk_days))
            url = candle_url(PRODUCTS[asset], cursor, finish)
            content = downloader(url)
            parsed = parse_candles(content, cursor, finish)
            result[asset].update(parsed)
            inventory.append({
                "key": f"coinbase:{asset}:{index:02d}",
                "url": url,
                "raw_sha256": hashlib.sha256(content).hexdigest(),
                "rows": len(parsed),
            })
            cursor = finish + timedelta(days=1)
            index += 1
            time.sleep(0.04)
    common = set.intersection(*(set(result[asset]) for asset in ASSETS))
    ordered = sorted(common)
    if len(ordered) != CANDLE_COUNT:
        raise LearnedDailyV41Error(
            f"expected {CANDLE_COUNT} aligned candles, found {len(ordered)}"
        )
    for previous, current in zip(ordered, ordered[1:]):
        if current - previous != timedelta(days=1):
            raise LearnedDailyV41Error("daily candle gap")
    return ({
        asset: {stamp: result[asset][stamp] for stamp in ordered}
        for asset in ASSETS
    }, inventory)


def safe_std(values: np.ndarray) -> float:
    return max(float(np.std(values)), 1e-9)


def efficiency(values: np.ndarray) -> float:
    movement = float(np.sum(np.abs(np.diff(values))))
    return abs(float(values[-1] - values[0])) / max(movement, 1e-12)


def feature_names() -> list[str]:
    names = [
        *(f"return_{days}" for days in (1, 3, 7, 14, 30, 60, 120, 200)),
        "volatility_7", "volatility_30", "volatility_90",
        "efficiency_14", "efficiency_60",
        *(f"sma_distance_{days}" for days in (20, 50, 100, 200)),
        "daily_range", "close_location", "volume_z_30", "quote_volume_30",
        "beta_30", "beta_90", "corr_30", "corr_90",
        "relative_strength_7", "relative_strength_30", "relative_strength_90",
        "btc_return_7", "btc_return_30", "btc_volatility_30",
        "market_return_7", "market_return_30", "market_return_90",
        "breadth_20", "breadth_50", "breadth_200", "dispersion_30",
        "median_volume_z", "average_correlation_30",
    ]
    names.extend(f"asset_{asset}" for asset in ASSETS)
    return list(names)


def _arrays(
    bars: dict[str, dict[datetime, Bar]],
) -> tuple[list[datetime], dict[str, dict[str, np.ndarray]]]:
    dates = sorted(next(iter(bars.values())))
    arrays: dict[str, dict[str, np.ndarray]] = {}
    for asset in ASSETS:
        rows = [bars[asset][date] for date in dates]
        arrays[asset] = {
            "open": np.asarray([row.open for row in rows], dtype=float),
            "high": np.asarray([row.high for row in rows], dtype=float),
            "low": np.asarray([row.low for row in rows], dtype=float),
            "close": np.asarray([row.close for row in rows], dtype=float),
            "volume": np.asarray([row.volume for row in rows], dtype=float),
        }
    return dates, arrays


def _rolling_corr(a: np.ndarray, b: np.ndarray) -> float:
    value = float(np.corrcoef(a, b)[0, 1])
    return value if math.isfinite(value) else 0.0


def build_dataset(bars: dict[str, dict[datetime, Bar]]) -> Dataset:
    dates, arrays = _arrays(bars)
    closes = np.vstack([arrays[asset]["close"] for asset in ASSETS])
    opens = np.vstack([arrays[asset]["open"] for asset in ASSETS])
    lows = np.vstack([arrays[asset]["low"] for asset in ASSETS])
    returns1 = np.diff(np.log(closes), axis=1, prepend=np.log(closes[:, :1]))
    rows: list[list[float]] = []
    labels = {horizon: [] for horizon in HORIZONS}
    opportunities = {horizon: [] for horizon in HORIZONS}
    downside3: list[int] = []
    regimes: list[int] = []
    row_dates: list[datetime] = []
    row_assets: list[str] = []
    liquidity: list[float] = []

    for index in range(WARMUP - 1, len(dates) - 8):
        market_close = np.mean(closes, axis=0)
        market_returns = {
            days: float(market_close[index] / market_close[index - days] - 1.0)
            for days in (7, 30, 90)
        }
        breadth = {
            days: float(np.mean([
                closes[pos, index] > np.mean(closes[pos, index - days + 1:index + 1])
                for pos in range(len(ASSETS))
            ]))
            for days in (20, 50, 200)
        }
        volume_zs: list[float] = []
        for asset in ASSETS:
            quote = arrays[asset]["volume"] * arrays[asset]["close"]
            logged = np.log1p(quote[index - 29:index + 1])
            volume_zs.append(float((logged[-1] - np.mean(logged)) / safe_std(logged)))
        corr_values = []
        for left in range(len(ASSETS)):
            for right in range(left + 1, len(ASSETS)):
                corr_values.append(_rolling_corr(
                    returns1[left, index - 29:index + 1],
                    returns1[right, index - 29:index + 1],
                ))
        average_correlation = float(np.mean(corr_values))
        dispersion30 = float(np.std(
            closes[:, index] / closes[:, index - 30] - 1.0
        ))
        btc_r7 = float(closes[0, index] / closes[0, index - 7] - 1.0)
        btc_r30 = float(closes[0, index] / closes[0, index - 30] - 1.0)
        btc_vol30 = safe_std(returns1[0, index - 29:index + 1])

        future_market = {
            horizon: float(np.mean(
                opens[:, index + horizon + 1] / opens[:, index + 1] - 1.0
            ))
            for horizon in HORIZONS
        }
        market_path3 = np.mean(lows[:, index + 1:index + 4], axis=0)
        market_entry = float(np.mean(opens[:, index + 1]))
        market_draw3 = float(np.min(market_path3 / market_entry - 1.0))
        if market_draw3 <= -0.025:
            regime = 2
        elif market_returns[30] > 0.04 and breadth[50] >= 0.6:
            regime = 1
        elif market_returns[30] < -0.05 and market_returns[7] > 0.0:
            regime = 3
        else:
            regime = 0

        for pos, asset in enumerate(ASSETS):
            values = arrays[asset]
            close = values["close"]
            asset_r = returns1[pos]
            asset_returns = {
                days: float(close[index] / close[index - days] - 1.0)
                for days in (1, 3, 7, 14, 30, 60, 120, 200)
            }
            quote = values["volume"] * close
            quote_window = quote[index - 29:index + 1]
            logged_quote = np.log1p(quote_window)
            beta_corr: list[float] = []
            for window in (30, 90):
                left = asset_r[index - window + 1:index + 1]
                right = returns1[0, index - window + 1:index + 1]
                covariance = float(np.cov(left, right, ddof=0)[0, 1])
                beta_corr.extend([
                    covariance / max(float(np.var(right)), 1e-12),
                    _rolling_corr(left, right),
                ])
            daily_range = float((values["high"][index] - values["low"][index]) / close[index])
            close_location = float(
                (close[index] - values["low"][index])
                / max(values["high"][index] - values["low"][index], 1e-12)
                - 0.5
            )

            row = [
                *(asset_returns[days] for days in (1, 3, 7, 14, 30, 60, 120, 200)),
                safe_std(asset_r[index - 6:index + 1]),
                safe_std(asset_r[index - 29:index + 1]),
                safe_std(asset_r[index - 89:index + 1]),
                efficiency(close[index - 13:index + 1]),
                efficiency(close[index - 59:index + 1]),
                *(float(close[index] / np.mean(close[index - days + 1:index + 1]) - 1.0)
                  for days in (20, 50, 100, 200)),
                daily_range,
                close_location,
                float((logged_quote[-1] - np.mean(logged_quote)) / safe_std(logged_quote)),
                float(np.mean(quote_window)),
                beta_corr[0], beta_corr[2], beta_corr[1], beta_corr[3],
                asset_returns[7] - market_returns[7],
                asset_returns[30] - market_returns[30],
                asset_returns[90] if 90 in asset_returns else float(close[index] / close[index - 90] - 1.0) - market_returns[90],
                btc_r7, btc_r30, btc_vol30,
                market_returns[7], market_returns[30], market_returns[90],
                breadth[20], breadth[50], breadth[200], dispersion30,
                float(np.median(volume_zs)), average_correlation,
            ]
            row.extend(1.0 if asset == candidate else 0.0 for candidate in ASSETS)
            entry = float(values["open"][index + 1])
            for horizon in HORIZONS:
                actual = float(values["open"][index + horizon + 1] / entry - 1.0)
                labels[horizon].append(actual)
                opportunities[horizon].append(int(actual > 2.0 * STRESS_ONE_WAY_COST))
            path_low = float(np.min(values["low"][index + 1:index + 4] / entry - 1.0))
            downside3.append(int(path_low <= -0.02))
            regimes.append(regime)
            rows.append(row)
            row_dates.append(dates[index])
            row_assets.append(asset)
            liquidity.append(float(np.mean(quote_window)))

    X = np.asarray(rows, dtype=float)
    if not np.all(np.isfinite(X)):
        raise LearnedDailyV41Error("nonfinite feature matrix")
    return Dataset(
        X=X,
        returns={horizon: np.asarray(values, dtype=float) for horizon, values in labels.items()},
        opportunities={horizon: np.asarray(values, dtype=int) for horizon, values in opportunities.items()},
        downside3=np.asarray(downside3, dtype=int),
        regimes=np.asarray(regimes, dtype=int),
        dates=row_dates,
        assets=row_assets,
        feature_names=feature_names(),
        quote_volume_30=np.asarray(liquidity, dtype=float),
    )


def chronological_masks(dataset: Dataset) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    unique_dates = sorted(set(dataset.dates))
    train_end = unique_dates[int(len(unique_dates) * 0.70)]
    calibration_end = unique_dates[int(len(unique_dates) * 0.85)]
    values = np.asarray([int(date.timestamp()) for date in dataset.dates], dtype=np.int64)
    return (
        values < int(train_end.timestamp()),
        (values >= int(train_end.timestamp())) & (values < int(calibration_end.timestamp())),
        values >= int(calibration_end.timestamp()),
    )


def model_grid() -> list[dict[str, Any]]:
    return [
        {"learning_rate": rate, "max_leaf_nodes": leaves, "max_iter": 120}
        for rate in (0.04, 0.08)
        for leaves in (15, 31)
    ]


def _positive_probability(model: Any, X: np.ndarray) -> np.ndarray:
    probabilities = model.predict_proba(X)
    classes = list(model.classes_)
    if 1 not in classes:
        return np.zeros(len(X), dtype=float)
    return probabilities[:, classes.index(1)]


def predict_bundle(bundle: Bundle, X: np.ndarray) -> dict[str, Any]:
    returns: dict[int, np.ndarray] = {}
    opportunities: dict[int, np.ndarray] = {}
    disagreements: list[np.ndarray] = []
    for horizon in HORIZONS:
        matrix = np.vstack([model.predict(X) for model in bundle.return_models[horizon]])
        returns[horizon] = np.mean(matrix, axis=0)
        disagreements.append(np.std(matrix, axis=0))
        opportunity_matrix = np.vstack([
            _positive_probability(model, X)
            for model in bundle.opportunity_models[horizon]
        ])
        opportunities[horizon] = np.mean(opportunity_matrix, axis=0)
        disagreements.append(np.std(opportunity_matrix, axis=0))
    downside_matrix = np.vstack([
        _positive_probability(model, X) for model in bundle.downside_models
    ])
    disagreement = np.sqrt(np.sum(np.vstack(disagreements) ** 2, axis=0))
    return {
        "returns": returns,
        "opportunities": opportunities,
        "downside3": np.mean(downside_matrix, axis=0),
        "disagreement": disagreement,
        "regime": bundle.regime_model.predict(X),
    }


def decisions_by_date(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: Bundle,
    predictions: dict[str, Any],
) -> dict[datetime, dict[str, Any]]:
    grouped: dict[datetime, list[int]] = {}
    for index in np.flatnonzero(mask):
        grouped.setdefault(dataset.dates[int(index)], []).append(int(index))
    result: dict[datetime, dict[str, Any]] = {}
    for date in sorted(grouped):
        indexes = grouped[date]
        regime_values = [int(predictions["regime"][index]) for index in indexes]
        regime = max(set(regime_values), key=regime_values.count)
        ranked: list[tuple[float, int]] = []
        if regime != 2:
            for index in indexes:
                horizon_ok = [
                    predictions["returns"][horizon][index] > 2.0 * STRESS_ONE_WAY_COST
                    and predictions["opportunities"][horizon][index]
                    >= bundle.opportunity_threshold
                    for horizon in HORIZONS
                ]
                if sum(horizon_ok) < bundle.required_positive_horizons:
                    continue
                if predictions["returns"][7][index] <= 0.0:
                    continue
                if float(predictions["downside3"][index]) > 0.45:
                    continue
                if float(predictions["disagreement"][index]) > bundle.uncertainty_threshold:
                    continue
                if float(dataset.quote_volume_30[index]) < bundle.liquidity_threshold:
                    continue
                score = (
                    0.20 * float(predictions["returns"][1][index])
                    + 0.50 * float(predictions["returns"][3][index])
                    + 0.30 * float(predictions["returns"][7][index])
                    - 0.35 * float(predictions["downside3"][index])
                    - 0.50 * float(predictions["disagreement"][index])
                )
                ranked.append((score, index))
        selected = [index for _, index in sorted(ranked, reverse=True)[:2]]
        result[date] = {"regime": regime, "selected": selected}
    return result


def simulate(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: Bundle,
    predictions: dict[str, Any],
    *,
    one_way_cost: float,
) -> dict[str, Any]:
    decisions = decisions_by_date(dataset, mask, bundle, predictions)
    index_map = {(dataset.dates[i], dataset.assets[i]): i for i in np.flatnonzero(mask)}
    cash = 1.0
    holdings = {asset: 0.0 for asset in ASSETS}
    peak = 1.0
    maximum_drawdown = 0.0
    turnover = 0.0
    action_count = 0
    age = 3
    selected_assets: tuple[str, ...] = ()
    daily_returns: list[float] = []
    contributions = {asset: 0.0 for asset in ASSETS}
    selected_ever: set[str] = set()

    for date in sorted(decisions):
        equity_before = cash + sum(holdings.values())
        decision = decisions[date]
        panic = decision["regime"] == 2
        due = age >= 3
        target_assets = selected_assets
        if panic:
            target_assets = ()
        elif due:
            target_assets = tuple(dataset.assets[index] for index in decision["selected"])

        if panic or due:
            target_values = {
                asset: (0.05 * equity_before if asset in target_assets else 0.0)
                for asset in ASSETS
            }
            traded = sum(abs(target_values[asset] - holdings[asset]) for asset in ASSETS)
            if traded > 1e-12:
                cost = one_way_cost * traded
                cash -= cost
                turnover += traded
                action_count += 1
            cash += sum(holdings[asset] - target_values[asset] for asset in ASSETS)
            holdings = target_values
            selected_assets = target_assets
            selected_ever.update(target_assets)
            age = 0

        equity_open = cash + sum(holdings.values())
        for asset in ASSETS:
            if holdings[asset] <= 0.0:
                continue
            index = index_map[(date, asset)]
            asset_return = float(dataset.returns[1][index])
            contribution = holdings[asset] * asset_return
            holdings[asset] *= 1.0 + asset_return
            contributions[asset] += contribution
        equity_close = cash + sum(holdings.values())
        daily_returns.append(equity_close / max(equity_open, 1e-12) - 1.0)
        peak = max(peak, equity_close)
        maximum_drawdown = max(maximum_drawdown, 1.0 - equity_close / peak)
        age += 1

    terminal_equity = cash + sum(holdings.values())
    liquidation = sum(holdings.values())
    if liquidation > 0.0:
        cost = one_way_cost * liquidation
        cash += liquidation - cost
        turnover += liquidation
        holdings = {asset: 0.0 for asset in ASSETS}
    return {
        "net_return": cash - 1.0,
        "maximum_drawdown": maximum_drawdown,
        "turnover": turnover,
        "target_changing_actions": action_count,
        "selected_assets": sorted(selected_ever),
        "asset_contribution": contributions,
        "daily_returns": daily_returns,
        "decision_count": len(decisions),
        "terminal_equity_before_liquidation": terminal_equity,
    }


def train_bundle(dataset: Dataset) -> tuple[Bundle, dict[str, Any]]:
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

    train_mask, calibration_mask, _ = chronological_masks(dataset)
    best: tuple[float, Bundle, dict[str, Any]] | None = None
    for config in model_grid():
        return_models = {
            horizon: [
                HistGradientBoostingRegressor(
                    **config, l2_regularization=0.1, random_state=seed
                ).fit(dataset.X[train_mask], dataset.returns[horizon][train_mask])
                for seed in (17, 41, 83)
            ]
            for horizon in HORIZONS
        }
        opportunity_models = {
            horizon: [
                HistGradientBoostingClassifier(
                    **config, l2_regularization=0.1, random_state=seed
                ).fit(dataset.X[train_mask], dataset.opportunities[horizon][train_mask])
                for seed in (19, 43, 89)
            ]
            for horizon in HORIZONS
        }
        downside_models = [
            HistGradientBoostingClassifier(
                **config, l2_regularization=0.1, random_state=seed
            ).fit(dataset.X[train_mask], dataset.downside3[train_mask])
            for seed in (23, 47, 97)
        ]
        regime_model = HistGradientBoostingClassifier(
            **config, l2_regularization=0.1, random_state=101
        ).fit(dataset.X[train_mask], dataset.regimes[train_mask])
        provisional = Bundle(
            return_models, opportunity_models, downside_models, regime_model,
            config, 0.35, 2, float("inf"), 0.0, dataset.feature_names,
        )
        predictions = predict_bundle(provisional, dataset.X)
        uncertainty_threshold = float(np.quantile(
            predictions["disagreement"][calibration_mask], 0.75
        ))
        liquidity_threshold = float(np.quantile(
            dataset.quote_volume_30[calibration_mask], 0.10
        ))
        for opportunity_threshold in (0.35, 0.45, 0.55):
            for required_horizons in (2, 3):
                bundle = Bundle(
                    return_models, opportunity_models, downside_models, regime_model,
                    config, opportunity_threshold, required_horizons,
                    uncertainty_threshold, liquidity_threshold, dataset.feature_names,
                )
                summary = simulate(
                    dataset, calibration_mask, bundle, predictions,
                    one_way_cost=STANDARD_ONE_WAY_COST,
                )
                score = (
                    summary["net_return"]
                    - 2.0 * summary["maximum_drawdown"]
                    - 0.25 * summary["turnover"]
                )
                if summary["target_changing_actions"] < 8:
                    score -= 1.0
                if best is None or score > best[0]:
                    best = (score, bundle, summary)
    assert best is not None
    return best[1], {"calibration_score": best[0], "calibration_summary": best[2]}


def evaluate_bundle(dataset: Dataset, bundle: Bundle) -> dict[str, Any]:
    _, _, test_mask = chronological_masks(dataset)
    predictions = predict_bundle(bundle, dataset.X)
    standard = simulate(dataset, test_mask, bundle, predictions, one_way_cost=STANDARD_ONE_WAY_COST)
    stress = simulate(dataset, test_mask, bundle, predictions, one_way_cost=STRESS_ONE_WAY_COST)
    test_dates = sorted({dataset.dates[i] for i in np.flatnonzero(test_mask)})
    windows_standard: list[float] = []
    windows_stress: list[float] = []
    window_contributions: list[float] = []
    for dates_part in np.array_split(np.asarray(test_dates, dtype=object), 5):
        date_set = set(dates_part.tolist())
        window_mask = np.asarray([
            bool(test_mask[i] and dataset.dates[i] in date_set)
            for i in range(len(dataset.dates))
        ])
        std_part = simulate(dataset, window_mask, bundle, predictions, one_way_cost=STANDARD_ONE_WAY_COST)
        stress_part = simulate(dataset, window_mask, bundle, predictions, one_way_cost=STRESS_ONE_WAY_COST)
        windows_standard.append(std_part["net_return"])
        windows_stress.append(stress_part["net_return"])
        window_contributions.append(max(0.0, std_part["net_return"]))
    span_days = (test_dates[-1] - test_dates[0]).days if len(test_dates) > 1 else 0
    annualized = (
        (1.0 + standard["net_return"]) ** (365.0 / span_days) - 1.0
        if span_days > 0 and standard["net_return"] > -1.0 else -1.0
    )
    positive_assets = [max(0.0, value) for value in standard["asset_contribution"].values()]
    asset_share = max(positive_assets) / sum(positive_assets) if sum(positive_assets) > 0 else 1.0
    window_share = max(window_contributions) / sum(window_contributions) if sum(window_contributions) > 0 else 1.0
    selected = set(standard["selected_assets"])
    gates = {
        "annualized_net_at_least_five_percent": annualized >= 0.05,
        "stress_compounded_positive": stress["net_return"] > 0.0,
        "drawdown_cap": max(standard["maximum_drawdown"], stress["maximum_drawdown"]) <= 0.10,
        "five_positive_standard_windows": all(value > 0.0 for value in windows_standard),
        "four_positive_stress_windows": sum(value > 0.0 for value in windows_stress) >= 4,
        "twenty_target_changes": standard["target_changing_actions"] >= 20,
        "asset_diversity": "BTC" in selected and len(selected - {"BTC"}) >= 2,
        "asset_concentration": asset_share <= 0.70,
        "window_concentration": window_share <= 0.70,
        "minimum_verification_span_days": span_days >= 90,
        "independent_source_replication": False,
    }
    preliminary = all(value for key, value in gates.items() if key != "independent_source_replication")
    return {
        "standard": standard, "stress": stress,
        "five_window_standard_returns": windows_standard,
        "five_window_stress_returns": windows_stress,
        "verification_span_days": span_days,
        "annualized_standard_return": annualized,
        "maximum_positive_asset_share": asset_share,
        "maximum_positive_window_share": window_share,
        "gates": gates,
        "status": "HISTORICAL_BREAKTHROUGH_PENDING_REPLICATION" if preliminary else "NOT_YET_HISTORICAL_BREAKTHROUGH",
    }


def latest_feature_matrix(
    bars: dict[str, dict[datetime, Bar]],
) -> tuple[datetime, np.ndarray, np.ndarray]:
    dates, arrays = _arrays(bars)
    index = len(dates) - 1
    closes = np.vstack([arrays[asset]["close"] for asset in ASSETS])
    returns1 = np.diff(np.log(closes), axis=1, prepend=np.log(closes[:, :1]))
    market_close = np.mean(closes, axis=0)
    market_returns = {
        days: float(market_close[index] / market_close[index - days] - 1.0)
        for days in (7, 30, 90)
    }
    breadth = {
        days: float(np.mean([
            closes[pos, index] > np.mean(closes[pos, index - days + 1:index + 1])
            for pos in range(len(ASSETS))
        ])) for days in (20, 50, 200)
    }
    volume_zs = []
    for asset in ASSETS:
        quote = arrays[asset]["volume"] * arrays[asset]["close"]
        logged = np.log1p(quote[index - 29:index + 1])
        volume_zs.append(float((logged[-1] - np.mean(logged)) / safe_std(logged)))
    correlations = [
        _rolling_corr(returns1[left, index - 29:index + 1], returns1[right, index - 29:index + 1])
        for left in range(len(ASSETS)) for right in range(left + 1, len(ASSETS))
    ]
    average_correlation = float(np.mean(correlations))
    dispersion30 = float(np.std(closes[:, index] / closes[:, index - 30] - 1.0))
    btc_r7 = float(closes[0, index] / closes[0, index - 7] - 1.0)
    btc_r30 = float(closes[0, index] / closes[0, index - 30] - 1.0)
    btc_vol30 = safe_std(returns1[0, index - 29:index + 1])
    rows: list[list[float]] = []
    liquidity: list[float] = []

    for pos, asset in enumerate(ASSETS):
        values = arrays[asset]
        close = values["close"]
        asset_r = returns1[pos]
        asset_returns = {
            days: float(close[index] / close[index - days] - 1.0)
            for days in (1, 3, 7, 14, 30, 60, 120, 200)
        }
        quote = values["volume"] * close
        quote_window = quote[index - 29:index + 1]
        logged_quote = np.log1p(quote_window)
        beta_corr: list[float] = []
        for window in (30, 90):
            left = asset_r[index - window + 1:index + 1]
            right = returns1[0, index - window + 1:index + 1]
            covariance = float(np.cov(left, right, ddof=0)[0, 1])
            beta_corr.extend([
                covariance / max(float(np.var(right)), 1e-12),
                _rolling_corr(left, right),
            ])
        high = values["high"][index]
        low = values["low"][index]
        row = [
            *(asset_returns[days] for days in (1, 3, 7, 14, 30, 60, 120, 200)),
            safe_std(asset_r[index - 6:index + 1]),
            safe_std(asset_r[index - 29:index + 1]),
            safe_std(asset_r[index - 89:index + 1]),
            efficiency(close[index - 13:index + 1]),
            efficiency(close[index - 59:index + 1]),
            *(float(close[index] / np.mean(close[index - days + 1:index + 1]) - 1.0)
              for days in (20, 50, 100, 200)),
            float((high - low) / close[index]),
            float((close[index] - low) / max(high - low, 1e-12) - 0.5),
            float((logged_quote[-1] - np.mean(logged_quote)) / safe_std(logged_quote)),
            float(np.mean(quote_window)),
            beta_corr[0], beta_corr[2], beta_corr[1], beta_corr[3],
            asset_returns[7] - market_returns[7],
            asset_returns[30] - market_returns[30],
            float(close[index] / close[index - 90] - 1.0) - market_returns[90],
            btc_r7, btc_r30, btc_vol30,
            market_returns[7], market_returns[30], market_returns[90],
            breadth[20], breadth[50], breadth[200], dispersion30,
            float(np.median(volume_zs)), average_correlation,
        ]
        row.extend(1.0 if asset == candidate else 0.0 for candidate in ASSETS)
        rows.append(row)
        liquidity.append(float(np.mean(quote_window)))
    X = np.asarray(rows, dtype=float)
    if not np.all(np.isfinite(X)):
        raise LearnedDailyV41Error("nonfinite latest features")
    return dates[index], X, np.asarray(liquidity, dtype=float)


def current_reasoning(bundle: Bundle, bars: dict[str, dict[datetime, Bar]]) -> dict[str, Any]:
    date, X, liquidity = latest_feature_matrix(bars)
    predictions = predict_bundle(bundle, X)
    regime_values = [int(value) for value in predictions["regime"]]
    regime = max(set(regime_values), key=regime_values.count)
    regime_names = {0: "chop", 1: "trend", 2: "panic", 3: "recovery"}
    candidates: list[dict[str, Any]] = []
    for index, asset in enumerate(ASSETS):
        horizon_ok = {
            horizon: bool(
                predictions["returns"][horizon][index] > 2.0 * STRESS_ONE_WAY_COST
                and predictions["opportunities"][horizon][index]
                >= bundle.opportunity_threshold
            ) for horizon in HORIZONS
        }
        eligible = (
            regime != 2
            and sum(horizon_ok.values()) >= bundle.required_positive_horizons
            and float(predictions["returns"][7][index]) > 0.0
            and float(predictions["downside3"][index]) <= 0.45
            and float(predictions["disagreement"][index]) <= bundle.uncertainty_threshold
            and float(liquidity[index]) >= bundle.liquidity_threshold
        )
        score = (
            0.20 * float(predictions["returns"][1][index])
            + 0.50 * float(predictions["returns"][3][index])
            + 0.30 * float(predictions["returns"][7][index])
            - 0.35 * float(predictions["downside3"][index])
            - 0.50 * float(predictions["disagreement"][index])
        )
        candidates.append({
            "asset": asset,
            "expected_returns": {
                str(horizon): float(predictions["returns"][horizon][index])
                for horizon in HORIZONS
            },
            "opportunity_probabilities": {
                str(horizon): float(predictions["opportunities"][horizon][index])
                for horizon in HORIZONS
            },
            "positive_horizons": horizon_ok,
            "downside_probability_3d": float(predictions["downside3"][index]),
            "ensemble_disagreement": float(predictions["disagreement"][index]),
            "liquidity_proxy": float(liquidity[index]),
            "score": score,
            "eligible": bool(eligible),
        })
    ranked = sorted([row for row in candidates if row["eligible"]], key=lambda row: row["score"], reverse=True)[:2]
    targets = {row["asset"]: 0.05 for row in ranked}
    return {
        "completed_candle_date_utc": date.date().isoformat(),
        "regime": regime_names[regime],
        "decision": "ALLOCATE" if targets else "CASH",
        "target_weights": targets,
        "minimum_cash_weight": 1.0 - sum(targets.values()),
        "candidates": sorted(candidates, key=lambda row: row["score"], reverse=True),
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": True,
    }


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def run_historical(as_of: datetime | None = None) -> tuple[
    dict[str, Any], Bundle, dict[str, dict[datetime, Bar]]
]:
    if not PROTOCOL_PATH.is_file() or not CONTRACT_PATH.is_file():
        raise LearnedDailyV41Error("missing frozen v4.1 protocol")
    end = completed_day(as_of or datetime.now(timezone.utc))
    bars, inventory = fetch_history(end)
    dataset = build_dataset(bars)
    bundle, selection = train_bundle(dataset)
    evaluation = evaluate_bundle(dataset, bundle)
    reasoning = current_reasoning(bundle, bars)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": "DAILY_MULTI_HORIZON_LEARNED_HISTORICAL_AND_CURRENT_PAPER_ONLY",
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": True,
        "changes_track_a": False,
        "assets": list(ASSETS),
        "products": PRODUCTS,
        "data_end_utc": utc_iso(end),
        "candle_count": CANDLE_COUNT,
        "horizons_days": list(HORIZONS),
        "feature_names": dataset.feature_names,
        "selected_config": bundle.config,
        "opportunity_threshold": bundle.opportunity_threshold,
        "required_positive_horizons": bundle.required_positive_horizons,
        "uncertainty_threshold": bundle.uncertainty_threshold,
        "liquidity_threshold": bundle.liquidity_threshold,
        "selection": selection,
        "evaluation": evaluation,
        "current_reasoning": reasoning,
        "source_inventory": inventory,
        "source_inventory_sha256": hashlib.sha256(canonical_json(inventory).encode()).hexdigest(),
        "protocol_sha256": hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest(),
        "contract_sha256": hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest(),
    }
    report["report_sha256"] = hashlib.sha256(canonical_json(report).encode()).hexdigest()
    return report, bundle, bars


def portable_state(report: dict[str, Any], bundle: Bundle) -> dict[str, Any]:
    return {
        "historical": report,
        "bundle": {
            "return_models": bundle.return_models,
            "opportunity_models": bundle.opportunity_models,
            "downside_models": bundle.downside_models,
            "regime_model": bundle.regime_model,
            "config": bundle.config,
            "opportunity_threshold": bundle.opportunity_threshold,
            "required_positive_horizons": bundle.required_positive_horizons,
            "uncertainty_threshold": bundle.uncertainty_threshold,
            "liquidity_threshold": bundle.liquidity_threshold,
            "feature_names": bundle.feature_names,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run v4.1 daily learned paper research")
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--bundle-out")
    parser.add_argument("--as-of-utc")
    args = parser.parse_args(argv)
    as_of = (
        datetime.fromisoformat(args.as_of_utc.replace("Z", "+00:00"))
        if args.as_of_utc else None
    )
    report, bundle, _ = run_historical(as_of)
    if args.bundle_out:
        import joblib
        path = Path(args.bundle_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(portable_state(report, bundle), path)
    output = Path(args.json_out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["evaluation"]["status"],
        "decision": report["current_reasoning"]["decision"],
        "targets": report["current_reasoning"]["target_weights"],
        "report_sha256": report["report_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
