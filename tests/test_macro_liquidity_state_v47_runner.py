from __future__ import annotations

from tradebot.research import macro_liquidity_state_v47 as model
from tradebot.research import macro_liquidity_state_v47_runner as runner


def plain_v44_summary(net_return: float = 0.01) -> dict:
    return {
        "net_return": net_return,
        "maximum_drawdown": 0.01,
        "turnover": 0.10,
        "target_changing_actions": 1,
        "selected_assets": ["BTC"],
        "asset_contribution": {asset: 0.0 for asset in model.ASSETS},
        "regime_contribution": {
            name: 0.0 for name in model.REGIME_NAMES.values()
        },
        "cash_contribution": 0.0,
        "decision_count": 1,
        "maximum_gross_exposure": 0.05,
        "maximum_target_exposure": 0.05,
    }


def gated_summary(net_return: float = 0.01) -> dict:
    return {
        **plain_v44_summary(net_return),
        "gated_assets": [],
        "gated_decision_count": 0,
        "maximum_selected_cardinality": 1,
        "never_added_asset": True,
    }


def test_baseline_gate_summary_adds_only_audit_fields():
    baseline = plain_v44_summary()
    normalized = runner.baseline_gate_summary(baseline)
    assert baseline.get("gated_decision_count") is None
    assert normalized["gated_decision_count"] == 0
    assert normalized["gated_assets"] == []
    assert normalized["maximum_selected_cardinality"] == 1
    assert normalized["never_added_asset"] is True
    assert normalized["net_return"] == baseline["net_return"]
    assert normalized["turnover"] == baseline["turnover"]


def test_disabled_selection_accepts_plain_v44_summaries():
    results = []
    for index in range(6):
        baseline = plain_v44_summary()
        results.append(model.FamilyFoldResult(
            fold=f"WF-{index + 1}",
            family="risk_appetite",
            threshold=None,
            training_date_count=200,
            positive_label_share=0.5,
            calibration_baseline=baseline,
            calibration_gated=gated_summary(),
            calibration_excess=0.0,
            validation_baseline=baseline,
            validation_gated=gated_summary(),
            validation_excess=0.0,
        ))

    selected, report = runner.select_macro_family({
        "risk_appetite": results,
    })

    assert selected == "disabled"
    assert report["selected_is_disabled_baseline"] is True
    assert report["folds"][0]["validation_gated"][
        "gated_decision_count"
    ] == 0
    assert report["folds"][0]["validation_gated"][
        "never_added_asset"
    ] is True


def test_runner_installs_selection_boundary(monkeypatch):
    monkeypatch.setattr(model, "select_macro_family", lambda _: ("old", {}))
    runner.install_compatibility_boundary()
    assert model.select_macro_family is runner.select_macro_family
