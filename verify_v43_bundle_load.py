import joblib
import sys

sys.path.insert(0, "src")
from tradebot.research import distributional_utility_v43 as model

raw = joblib.load("evidence/v43/bundle.joblib")
assert raw["authorizes_trading"] is False
assert isinstance(raw["bundle"], dict)
bundle = model.bundle_from_state(raw["bundle"])
assert bundle.top_n in (1, 2)
assert len(bundle.regime_models) >= 2
print({
    "portable": True,
    "top_n": bundle.top_n,
    "specialists": sorted(bundle.specialists),
    "regime_models": len(bundle.regime_models),
})
