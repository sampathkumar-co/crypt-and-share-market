from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import io
import json
import platform
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np

from tradebot.research import distributional_utility_v43 as v43
from tradebot.research.regime_ranking_v42 import (
    ASSETS,
    REGIME_NAMES,
    STANDARD_ONE_WAY_COST,
    STRESS_ONE_WAY_COST,
    Dataset,
    build_dataset,
    file_sha256,
    positive_share,
)
from tradebot.research.regime_ranking_v42_sources import (
    canonical_json,
    load_all_sources,
    utc_iso,
)

SCHEMA_VERSION = "4.4-yield-bearing-cash"
PROTOCOL_PATH = Path("research/V44_YIELD_BEARING_CASH_PROTOCOL.md")
CONTRACT_PATH = Path("research/V441_YIELD_BEARING_CASH_IMPLEMENTATION_CONTRACT.md")
FRED_CASH_URL = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv?"
    "id=DGS3MO&cosd=2022-01-01&coed=2026-06-30"
)
CASH_SERIES = "DGS3MO"
CASH_PROVIDER = "fred-federal-reserve-public-csv"
MIN_CASH_OBSERVATIONS = 1_000


class YieldBearingCashV44Error(RuntimeError):
    pass


@dataclass(frozen=True)
class CashRateHistory:
    annual_rates: dict[datetime, float]
    source: dict[str, Any]

    @property
    def dates(self) -> tuple[datetime, ...]:
        return tuple(sorted(self.annual_rates))


def parse_cash_rates(content: bytes) -> dict[datetime, float]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise YieldBearingCashV44Error("cash CSV is not UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text))
    fields = list(reader.fieldnames or [])
    date_column = (
        "observation_date"
        if "observation_date" in fields
        else "DATE"
        if "DATE" in fields
        else None
    )
    if date_column is None or CASH_SERIES not in fields:
        raise YieldBearingCashV44Error(
            f"cash CSV columns unavailable: {fields}"
        )
    rates: dict[datetime, float] = {}
    for row in reader:
        raw_rate = str(row.get(CASH_SERIES, "")).strip()
        if not raw_rate or raw_rate == ".":
            continue
        try:
            stamp = datetime.fromisoformat(
                str(row[date_column]).strip()
            ).replace(tzinfo=timezone.utc)
            annual_rate = float(raw_rate) / 100.0
        except (TypeError, ValueError) as exc:
            raise YieldBearingCashV44Error(
                f"invalid cash observation: {row}"
            ) from exc
        if stamp in rates:
            raise YieldBearingCashV44Error(
                f"duplicate cash observation: {stamp.date()}"
            )
        if not -0.10 < annual_rate < 0.30:
            raise YieldBearingCashV44Error(
                f"invalid annual cash rate on {stamp.date()}: {annual_rate}"
            )
        rates[stamp] = annual_rate
    if not rates:
        raise YieldBearingCashV44Error("cash CSV has no usable observations")
    return rates


def load_cash_history(
    *,
    url: str = FRED_CASH_URL,
    timeout: float = 30.0,
) -> CashRateHistory:
    request = Request(
        url,
        headers={
            "User-Agent": "tradebot-v44-yield-bearing-cash/1.0",
            "Accept": "text/csv,*/*;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            if response.status != 200:
                raise YieldBearingCashV44Error(
                    f"cash source returned HTTP {response.status}"
                )
            raw = response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise YieldBearingCashV44Error(
            f"cash source download failed: {exc}"
        ) from exc
    if not raw:
        raise YieldBearingCashV44Error("cash source returned an empty response")
    rates = parse_cash_rates(raw)
    if len(rates) < MIN_CASH_OBSERVATIONS:
        raise YieldBearingCashV44Error(
            f"cash source has only {len(rates)} observations; "
            f"minimum is {MIN_CASH_OBSERVATIONS}"
        )
    dates = sorted(rates)
    return CashRateHistory(
        annual_rates=rates,
        source={
            "provider": CASH_PROVIDER,
            "series": CASH_SERIES,
            "url": url,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "observation_count": len(rates),
            "first_date": dates[0].date().isoformat(),
            "last_date": dates[-1].date().isoformat(),
        },
    )


def annual_to_daily_rate(annual_rate: float) -> float:
    if annual_rate <= -1.0:
        raise YieldBearingCashV44Error(
            f"annual cash rate cannot compound: {annual_rate}"
        )
    return (1.0 + annual_rate) ** (1.0 / 365.0) - 1.0


def prior_known_annual_rate(
    history: CashRateHistory,
    stamp: datetime,
) -> tuple[datetime, float]:
    cutoff = stamp - timedelta(days=1)
    dates = history.dates
    index = bisect.bisect_right(dates, cutoff) - 1
    if index < 0:
        raise YieldBearingCashV44Error(
            f"No cash rate known by {utc_iso(stamp)}"
        )
    rate_date = dates[index]
    return rate_date, float(history.annual_rates[rate_date])


def simulate(
    dataset: Dataset,
    mask: np.ndarray,
    bundle: v43.Bundle,
    predictions: dict[str, Any],
    history: CashRateHistory,
    *,
    one_way_cost: float,
) -> dict[str, Any]:
    decisions = v43.decisions_by_date(dataset, mask, bundle, predictions)
    index_map = {
        (dataset.dates[index], dataset.assets[index]): index
        for index in np.flatnonzero(mask)
    }
    cash = 1.0
    holdings = {asset: 0.0 for asset in ASSETS}
    holding_regime = {asset: 0 for asset in ASSETS}
    selected_assets: tuple[str, ...] = ()
    selected_ever: set[str] = set()
    peak = 1.0
    maximum_drawdown = 0.0
    turnover = 0.0
    action_count = 0
    age = 3
    maximum_gross_exposure = 0.0
    maximum_target_exposure = 0.0
    daily_returns: list[float] = []
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    regime_contribution = {
        name: 0.0 for name in REGIME_NAMES.values()
    }
    cash_contribution = 0.0
    cash_rate_dates_used: set[datetime] = set()

    for stamp in sorted(decisions):
        equity_before = cash + sum(holdings.values())
        decision = decisions[stamp]
        panic = decision["regime"] == 2
        due = age >= 3
        target_assets = selected_assets
        if panic:
            target_assets = ()
        elif due:
            target_assets = tuple(
                dataset.assets[index]
                for index in decision["selected"]
            )

        if panic or due:
            target_values = {
                asset: (
                    0.05 * equity_before
                    if asset in target_assets
                    else 0.0
                )
                for asset in ASSETS
            }
            maximum_target_exposure = max(
                maximum_target_exposure,
                sum(target_values.values()) / max(equity_before, 1e-12),
            )
            traded = sum(
                abs(target_values[asset] - holdings[asset])
                for asset in ASSETS
            )
            changed = traded > 1e-12
            if changed:
                cost = one_way_cost * traded
                cash -= cost
                turnover += traded
                action_count += 1
            cash += sum(
                holdings[asset] - target_values[asset]
                for asset in ASSETS
            )
            holdings = target_values
            selected_assets = target_assets
            selected_ever.update(target_assets)
            for asset in target_assets:
                holding_regime[asset] = int(decision["regime"])
            if due or (panic and changed):
                age = 0

        equity_open = cash + sum(holdings.values())
        maximum_gross_exposure = max(
            maximum_gross_exposure,
            sum(holdings.values()) / max(equity_open, 1e-12),
        )

        rate_date, annual_rate = prior_known_annual_rate(history, stamp)
        cash_rate_dates_used.add(rate_date)
        cash_yield = cash * annual_to_daily_rate(annual_rate)
        cash += cash_yield
        cash_contribution += cash_yield

        for asset in ASSETS:
            if holdings[asset] <= 0.0:
                continue
            index = index_map[(stamp, asset)]
            asset_return = float(dataset.return1[index])
            contribution = holdings[asset] * asset_return
            holdings[asset] *= 1.0 + asset_return
            asset_contribution[asset] += contribution
            regime_name = REGIME_NAMES[holding_regime[asset]]
            regime_contribution[regime_name] += contribution
        equity_close = cash + sum(holdings.values())
        daily_returns.append(
            equity_close / max(equity_open, 1e-12) - 1.0
        )
        peak = max(peak, equity_close)
        maximum_drawdown = max(
            maximum_drawdown,
            1.0 - equity_close / peak,
        )
        age += 1

    terminal_equity_before_liquidation = cash + sum(holdings.values())
    liquidation = sum(holdings.values())
    if liquidation > 0.0:
        cost = one_way_cost * liquidation
        cash += liquidation - cost
        turnover += liquidation
        holdings = {asset: 0.0 for asset in ASSETS}
        maximum_drawdown = max(
            maximum_drawdown,
            1.0 - cash / max(peak, 1e-12),
        )

    return {
        "net_return": cash - 1.0,
        "maximum_drawdown": maximum_drawdown,
        "turnover": turnover,
        "target_changing_actions": action_count,
        "selected_assets": sorted(selected_ever),
        "asset_contribution": asset_contribution,
        "regime_contribution": regime_contribution,
        "cash_contribution": cash_contribution,
        "daily_returns": daily_returns,
        "decision_count": len(decisions),
        "maximum_gross_exposure": maximum_gross_exposure,
        "maximum_target_exposure": maximum_target_exposure,
        "terminal_equity_before_liquidation": (
            terminal_equity_before_liquidation
        ),
        "cash_rate_dates_used": len(cash_rate_dates_used),
    }


def _compound(values: list[float]) -> float:
    return float(np.prod([1.0 + value for value in values]) - 1.0)


def evaluate_sealed(
    dataset: Dataset,
    bundle: v43.Bundle,
    history: CashRateHistory,
    *,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    predictions = v43.predict_components(bundle, dataset.X)
    windows: list[dict[str, Any]] = []
    for name, start, end in v43.SEALED_WINDOWS:
        mask = v43.date_mask(dataset, start, end)
        standard = simulate(
            dataset,
            mask,
            bundle,
            predictions,
            history,
            one_way_cost=STANDARD_ONE_WAY_COST,
        )
        stress = simulate(
            dataset,
            mask,
            bundle,
            predictions,
            history,
            one_way_cost=STRESS_ONE_WAY_COST,
        )
        days = len({
            dataset.dates[index]
            for index in np.flatnonzero(mask)
        })
        standard["verification_days"] = days
        stress["verification_days"] = days
        windows.append({
            "name": name,
            "start": utc_iso(start),
            "end": utc_iso(end),
            "standard": standard,
            "stress": stress,
        })

    standard_returns = [
        float(value["standard"]["net_return"])
        for value in windows
    ]
    stress_returns = [
        float(value["stress"]["net_return"])
        for value in windows
    ]
    aggregate_standard = _compound(standard_returns)
    aggregate_stress = _compound(stress_returns)
    verification_days = sum(
        int(value["standard"]["verification_days"])
        for value in windows
    )
    annualized = (
        (1.0 + aggregate_standard) ** (365.0 / verification_days) - 1.0
        if verification_days > 0 and aggregate_standard > -1.0
        else -1.0
    )
    maximum_drawdown = max(
        max(
            float(value["standard"]["maximum_drawdown"]),
            float(value["stress"]["maximum_drawdown"]),
        )
        for value in windows
    )
    actions = sum(
        int(value["standard"]["target_changing_actions"])
        for value in windows
    )
    selected_assets = sorted(set().union(*[
        set(value["standard"]["selected_assets"])
        for value in windows
    ]))
    cash_contribution = sum(
        float(value["standard"]["cash_contribution"])
        for value in windows
    )
    asset_contribution = {asset: 0.0 for asset in ASSETS}
    regime_contribution = {
        name: 0.0 for name in REGIME_NAMES.values()
    }
    for value in windows:
        for asset, contribution in value["standard"][
            "asset_contribution"
        ].items():
            asset_contribution[asset] += float(contribution)
        for regime, contribution in value["standard"][
            "regime_contribution"
        ].items():
            regime_contribution[regime] += float(contribution)

    asset_share = positive_share(list(asset_contribution.values()))
    window_share = positive_share(standard_returns)
    regime_share = positive_share(list(regime_contribution.values()))
    gates = {
        "five_positive_standard_windows": all(
            value > 0.0 for value in standard_returns
        ),
        "four_positive_stress_windows": sum(
            value > 0.0 for value in stress_returns
        ) >= 4,
        "annualized_standard_at_least_five_percent": annualized >= 0.05,
        "aggregate_stress_positive": aggregate_stress > 0.0,
        "drawdown_cap": maximum_drawdown <= 0.10,
        "twenty_costed_actions": actions >= 20,
        "asset_diversity": (
            "BTC" in selected_assets
            and len(set(selected_assets) - {"BTC"}) >= 2
        ),
        "asset_concentration": asset_share <= 0.70,
        "window_concentration": window_share <= 0.70,
        "regime_concentration": regime_share <= 0.70,
        "independent_source_replication": False,
        "current_market_smoke": False,
        "untouched_historical_dates": False,
    }
    historical_only = all(
        value
        for key, value in gates.items()
        if key not in {
            "independent_source_replication",
            "current_market_smoke",
            "untouched_historical_dates",
        }
    )
    result: dict[str, Any] = {
        "windows": windows,
        "aggregate_standard_return": aggregate_standard,
        "aggregate_stress_return": aggregate_stress,
        "annualized_standard_return": annualized,
        "maximum_drawdown": maximum_drawdown,
        "verification_days": verification_days,
        "target_changing_actions": actions,
        "selected_assets": selected_assets,
        "asset_contribution": asset_contribution,
        "regime_contribution": regime_contribution,
        "cash_contribution": cash_contribution,
        "maximum_positive_asset_share": asset_share,
        "maximum_positive_window_share": window_share,
        "maximum_positive_regime_share": regime_share,
        "standard_window_returns": standard_returns,
        "stress_window_returns": stress_returns,
        "gates": gates,
        "retrospective": True,
        "status": (
            "RETROSPECTIVE_HISTORICAL_BREAKTHROUGH_PENDING_REPLICATION_AND_SMOKE"
            if historical_only
            else "RETROSPECTIVE_NOT_YET_BREAKTHROUGH"
        ),
    }
    if baseline is not None:
        result["v43_comparison"] = {
            "standard_return_uplift": (
                aggregate_standard
                - float(baseline["aggregate_standard_return"])
            ),
            "stress_return_uplift": (
                aggregate_stress
                - float(baseline["aggregate_stress_return"])
            ),
            "annualized_return_uplift": (
                annualized
                - float(baseline["annualized_standard_return"])
            ),
            "actions_unchanged": (
                actions == int(baseline["target_changing_actions"])
                and all(
                    int(window["standard"]["target_changing_actions"])
                    == int(base_window["standard"]["target_changing_actions"])
                    for window, base_window in zip(
                        windows,
                        baseline["windows"],
                        strict=True,
                    )
                )
            ),
            "selected_assets_unchanged": (
                selected_assets == list(baseline["selected_assets"])
                and all(
                    window["standard"]["selected_assets"]
                    == base_window["standard"]["selected_assets"]
                    for window, base_window in zip(
                        windows,
                        baseline["windows"],
                        strict=True,
                    )
                )
            ),
            "signal_or_risk_parameters_changed": False,
        }
    return result


def runtime_versions() -> dict[str, str]:
    import joblib
    import sklearn

    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scikit_learn": sklearn.__version__,
        "joblib": joblib.__version__,
    }


def run_campaign(
    states: dict[str, Any] | None = None,
    source_report: dict[str, Any] | None = None,
    cash_history: CashRateHistory | None = None,
    *,
    monthly_workers: int = 24,
    metrics_workers: int = 48,
) -> tuple[dict[str, Any], v43.Bundle]:
    if states is None:
        states, source_report = load_all_sources(
            monthly_workers=monthly_workers,
            metrics_workers=metrics_workers,
        )
    if source_report is None:
        source_report = {"schema_version": "synthetic-v44-source"}
    if cash_history is None:
        cash_history = load_cash_history()
    dataset = build_dataset(states)
    first_dataset_date = min(dataset.dates)
    if min(cash_history.annual_rates) > first_dataset_date:
        raise YieldBearingCashV44Error(
            "cash history starts after the research dataset"
        )
    bundle, calibration = v43.train_bundle(dataset)
    baseline = v43.evaluate_sealed(dataset, bundle)
    evaluation = evaluate_sealed(
        dataset,
        bundle,
        cash_history,
        baseline=baseline,
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_iso(datetime.now(timezone.utc)),
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": True,
        "retrospective": True,
        "universe": list(ASSETS),
        "source": source_report,
        "cash_source": cash_history.source,
        "runtime": runtime_versions(),
        "dataset": {
            "row_count": len(dataset.X),
            "date_count": len(set(dataset.dates)),
            "first_date": utc_iso(min(dataset.dates)),
            "last_date": utc_iso(max(dataset.dates)),
            "feature_count": len(dataset.feature_names),
            "training_end": utc_iso(v43.TRAIN_END),
            "calibration_start": utc_iso(v43.CALIBRATION_START),
            "calibration_end": utc_iso(v43.CALIBRATION_END),
        },
        "protocol_sha256": file_sha256(PROTOCOL_PATH),
        "implementation_contract_sha256": file_sha256(CONTRACT_PATH),
        "v43_protocol_sha256": file_sha256(v43.PROTOCOL_PATH),
        "v43_implementation_contract_sha256": file_sha256(
            v43.CONTRACT_PATH
        ),
        "bundle": v43.bundle_summary(bundle),
        "calibration": calibration,
        "v43_baseline": baseline,
        "evaluation": evaluation,
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report, bundle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run retrospective v4.4 yield-bearing cash paper research"
        )
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("evidence/v44/historical.json"),
    )
    parser.add_argument(
        "--bundle-out",
        type=Path,
        default=Path("evidence/v44/bundle.joblib"),
    )
    parser.add_argument("--monthly-workers", type=int, default=24)
    parser.add_argument("--metrics-workers", type=int, default=48)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report, bundle = run_campaign(
        monthly_workers=max(1, args.monthly_workers),
        metrics_workers=max(1, args.metrics_workers),
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    v43.save_bundle(args.bundle_out, bundle)
    print(json.dumps({
        "status": report["evaluation"]["status"],
        "report_sha256": report["report_sha256"],
        "standard_return": report["evaluation"][
            "aggregate_standard_return"
        ],
        "stress_return": report["evaluation"][
            "aggregate_stress_return"
        ],
        "annualized_standard_return": report["evaluation"][
            "annualized_standard_return"
        ],
        "cash_contribution": report["evaluation"][
            "cash_contribution"
        ],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
