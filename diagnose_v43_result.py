import json
from pathlib import Path

report = json.loads(Path("evidence/v43/historical.json").read_text())
evaluation = report["evaluation"]
summary = {
    "dataset": report["dataset"],
    "bundle": report["bundle"],
    "calibration": {
        "score": report["calibration"]["calibration_score"],
        "net_return": report["calibration"][
            "calibration_summary"
        ]["net_return"],
        "actions": report["calibration"][
            "calibration_summary"
        ]["target_changing_actions"],
        "maximum_drawdown": report["calibration"][
            "calibration_summary"
        ]["maximum_drawdown"],
    },
    "evaluation": {
        key: evaluation[key]
        for key in (
            "status",
            "aggregate_standard_return",
            "aggregate_stress_return",
            "annualized_standard_return",
            "maximum_drawdown",
            "verification_days",
            "target_changing_actions",
            "selected_assets",
            "maximum_positive_asset_share",
            "maximum_positive_window_share",
            "maximum_positive_regime_share",
            "standard_window_returns",
            "stress_window_returns",
            "gates",
        )
    },
}
summary["windows"] = [
    {
        "name": value["name"],
        "standard_return": value["standard"]["net_return"],
        "stress_return": value["stress"]["net_return"],
        "actions": value["standard"]["target_changing_actions"],
        "selected_assets": value["standard"]["selected_assets"],
        "maximum_drawdown": value["standard"]["maximum_drawdown"],
    }
    for value in evaluation["windows"]
]
print(json.dumps(summary, indent=2, sort_keys=True))
