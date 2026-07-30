from tradebot.backtest.crypto_multisource_holdout_artifact_materializer import (
    add_derived_display_fields,
)


def test_artifact_materializer_adds_only_derived_summary_fields() -> None:
    payload = {
        "accepted": False,
        "primary_beats_raw_fraction": 2 / 3,
        "variants": [
            {
                "variant": "primary_multisource",
                "average_return": 0.02,
                "periods": [{}, {}, {}],
            },
            {
                "variant": "raw_simple_trend",
                "average_return": 0.01,
                "periods": [{}, {}, {}],
            },
        ],
    }

    result = add_derived_display_fields(payload)

    assert result["accepted"] is False
    assert result["primary_average_improvement_vs_raw"] == 0.01
    assert result["primary_beats_raw_periods"] == 2
    assert result["variants"][0]["average_return"] == 0.02
