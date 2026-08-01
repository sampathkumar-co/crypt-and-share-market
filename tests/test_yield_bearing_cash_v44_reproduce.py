from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import joblib
import numpy as np
import pytest

from tradebot.research import yield_bearing_cash_v44_reproduce as model
from tradebot.research.regime_ranking_v42 import Dataset
from tradebot.research.regime_ranking_v42_sources import canonical_json, utc_iso


def dataset() -> Dataset:
    stamp = datetime(2025, 1, 2, tzinfo=timezone.utc)
    assets = list(model.ASSETS)
    size = len(assets)
    return Dataset(
        X=np.zeros((size, 2), dtype=float),
        return1=np.zeros(size),
        return3=np.zeros(size),
        return7=np.zeros(size),
        rank3=np.zeros(size),
        meta=np.ones(size, dtype=int),
        downside3=np.zeros(size, dtype=int),
        regimes=np.zeros(size, dtype=int),
        dates=[stamp] * size,
        assets=assets,
        feature_names=["x", "y"],
    )


def signed_baseline(
    source: dict,
    bundle_summary: dict,
    evaluation: dict,
    data: Dataset,
) -> dict:
    report = {
        "schema_version": model.v43.SCHEMA_VERSION,
        "paper_only": True,
        "authorizes_trading": False,
        "authorizes_shadow_paper": True,
        "source": source,
        "bundle": bundle_summary,
        "calibration": {"frozen": True},
        "dataset": {
            "row_count": len(data.X),
            "date_count": len(set(data.dates)),
            "first_date": utc_iso(min(data.dates)),
            "last_date": utc_iso(max(data.dates)),
            "feature_count": len(data.feature_names),
            "training_end": utc_iso(model.v43.TRAIN_END),
            "calibration_start": utc_iso(model.v43.CALIBRATION_START),
            "calibration_end": utc_iso(model.v43.CALIBRATION_END),
        },
        "evaluation": evaluation,
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def test_validate_baseline_report_rejects_tampering():
    data = dataset()
    report = signed_baseline(
        {"inventory_sha256": "abc"},
        {"top_n": 1},
        {"status": "frozen"},
        data,
    )
    report["evaluation"] = {"status": "tampered"}
    with pytest.raises(
        model.YieldBearingCashV44ReproductionError,
        match="SHA-256",
    ):
        model.validate_baseline_report(report)


def test_load_bundle_rejects_trading_authorization(tmp_path):
    path = tmp_path / "bundle.joblib"
    joblib.dump(
        {
            "schema_version": model.v43.SCHEMA_VERSION,
            "authorizes_trading": True,
            "bundle": {},
        },
        path,
    )
    with pytest.raises(
        model.YieldBearingCashV44ReproductionError,
        match="authorizes trading",
    ):
        model.load_bundle(path)


def test_reproduction_reuses_frozen_bundle_without_training(monkeypatch):
    data = dataset()
    source = {"inventory_sha256": "abc", "schema_version": "test"}
    bundle_summary = {"top_n": 1, "config": {"max_iter": 120}}
    baseline_evaluation = {
        "aggregate_standard_return": 0.004,
        "aggregate_stress_return": 0.001,
        "target_changing_actions": 51,
        "selected_assets": list(model.ASSETS),
    }
    overlay_evaluation = {
        "aggregate_standard_return": 0.031,
        "aggregate_stress_return": 0.027,
        "annualized_standard_return": 0.043,
        "maximum_drawdown": 0.011,
        "cash_contribution": 0.026,
        "status": "RETROSPECTIVE_NOT_YET_BREAKTHROUGH",
        "v43_comparison": {
            "standard_return_uplift": 0.027,
            "stress_return_uplift": 0.026,
            "annualized_return_uplift": 0.037,
            "actions_unchanged": True,
            "selected_assets_unchanged": True,
            "signal_or_risk_parameters_changed": False,
        },
    }
    baseline = signed_baseline(
        source,
        bundle_summary,
        baseline_evaluation,
        data,
    )
    bundle = object()
    cash = model.v44.CashRateHistory(
        annual_rates={
            datetime(2025, 1, 1, tzinfo=timezone.utc): 0.04,
        },
        source={
            "provider": model.v44.CASH_PROVIDER,
            "series": model.v44.CASH_SERIES,
            "observation_count": 1,
            "first_date": "2025-01-01",
            "last_date": "2025-01-01",
        },
    )

    monkeypatch.setattr(model, "build_dataset", lambda _states: data)
    monkeypatch.setattr(
        model.v43,
        "bundle_summary",
        lambda value: bundle_summary if value is bundle else {},
    )
    monkeypatch.setattr(
        model.v43,
        "evaluate_sealed",
        lambda current, value: baseline_evaluation,
    )
    monkeypatch.setattr(
        model.v44,
        "evaluate_sealed",
        lambda current, value, history, baseline: overlay_evaluation,
    )
    monkeypatch.setattr(
        model.v43,
        "train_bundle",
        lambda *_args, **_kwargs: pytest.fail("v4.3 retraining occurred"),
    )

    report = model.run_reproduction(
        baseline,
        bundle,
        states={"provided": True},
        source_report=source,
        cash_history=cash,
        baseline_bundle_sha256="bundle-sha",
    )
    assert report["comparison_with_v43"]["actions_unchanged"] is True
    assert report["reproduction"]["v43_retrained_for_overlay"] is False
    assert report["reproduction"]["v43_evaluation_exact"] is True
    assert report["baseline_bundle_sha256"] == "bundle-sha"
    assert report["untouched_historical_dates"] is False
