from __future__ import annotations

from dataclasses import asdict
from typing import Any

from tradebot.research import macro_liquidity_state_v47 as model


def baseline_gate_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Add v4.7 audit fields to an unchanged v4.4 simulation summary."""
    normalized = dict(summary)
    normalized.setdefault("gated_assets", [])
    normalized.setdefault("gated_decision_count", 0)
    normalized.setdefault(
        "maximum_selected_cardinality",
        min(2, len(normalized.get("selected_assets", []))),
    )
    normalized.setdefault("never_added_asset", True)
    return normalized


def select_macro_family(
    family_results: dict[str, list[model.FamilyFoldResult]],
) -> tuple[str, dict[str, Any]]:
    """Select a family while safely representing the disabled v4.4 fallback."""
    if not family_results:
        raise model.MacroLiquidityStateV47Error(
            "macro family selection requires at least one active family"
        )

    candidates: list[dict[str, Any]] = []
    best: tuple[tuple[Any, ...], str] | None = None
    disabled_results: list[model.FamilyFoldResult] = []
    exemplar = next(iter(family_results.values()))
    for value in exemplar:
        disabled_results.append(model.FamilyFoldResult(
            fold=value.fold,
            family="disabled",
            threshold=None,
            training_date_count=value.training_date_count,
            positive_label_share=value.positive_label_share,
            calibration_baseline=value.calibration_baseline,
            calibration_gated=baseline_gate_summary(
                value.calibration_baseline
            ),
            calibration_excess=0.0,
            validation_baseline=value.validation_baseline,
            validation_gated=baseline_gate_summary(
                value.validation_baseline
            ),
            validation_excess=0.0,
        ))

    all_results = {"disabled": disabled_results, **family_results}
    for family, results in all_results.items():
        eligible, reasons = model.family_eligibility(family, results)
        key = (
            model.family_selection_key(family, results)
            if eligible
            else None
        )
        candidates.append({
            "family": family,
            "eligible": eligible,
            "ineligibility_reasons": reasons,
            "selection_key": list(key) if key is not None else None,
            "minimum_fold_excess": min(
                value.validation_excess for value in results
            ),
            "positive_excess_fold_count": sum(
                value.validation_excess > 0.0 for value in results
            ),
            "compounded_excess": model.compounded_excess(results),
            "gated_decision_count": sum(
                int(value.validation_gated["gated_decision_count"])
                for value in results
            ),
        })
        if eligible and (best is None or key > best[0]):
            best = (key, family)

    if best is None:
        raise model.MacroLiquidityStateV47Error(
            "macro family selection produced no disabled fallback"
        )
    selected_results = all_results[best[1]]
    return best[1], {
        "selected_family": best[1],
        "selected_is_disabled_baseline": best[1] == "disabled",
        "selected_key": list(best[0]),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "folds": [asdict(value) for value in selected_results],
    }


def install_compatibility_boundary() -> None:
    model.select_macro_family = select_macro_family


def main(argv: list[str] | None = None) -> int:
    install_compatibility_boundary()
    return model.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
