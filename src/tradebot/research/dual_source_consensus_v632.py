from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from tradebot.research.forward_alpha_v25 import canonical_json
from tradebot.research import dual_source_consensus_v63 as base


PROTOCOL_PATH = Path("research/V632_SEMANTIC_DEPENDENCY_FINGERPRINT_PROTOCOL.md")
COMMON_SOURCE_ADDENDUM_PATH = Path(
    "research/V633_COMMON_SOURCE_DISCOVERY_WINDOW_ADDENDUM.md"
)
SIGNAL_LAG_ADDENDUM_PATH = Path(
    "research/V634_COMMON_SOURCE_SIGNAL_LAG_ADDENDUM.md"
)
WARMUP_ADDENDUM_PATH = Path(
    "research/V635_COMMON_SOURCE_200D_WARMUP_ADDENDUM.md"
)
EXPECTED_SEMANTIC = {
    "v6.1": "e88af5a6342f67c181abaea6e33c8f95a93117f00d26277698916a3dec414dc9",
    "v6.2": "520bb66e8c84057317ed75be808697bbedc021ab385c5e55d85b4e63254f7b1b",
}
ARTIFACT_REPORT_SHA = {
    "v6.1": "b6f5e75957cf31f26d7ebe2d1f341d67901dd4dfb3ad3d3f6b10a4be3fe34692",
    "v6.2": "7763dfbb68441e496ee638e23e7bd2650bf433a0d63a332d3b02c94709b60d7e",
}
EXPECTED_STATUS = {
    "v6.1": "ENSEMBLE_REJECTED",
    "v6.2": "CONSENSUS_ENSEMBLE_REJECTED",
}
COMMON_SOURCE_DISCOVERY_START = datetime(2020, 7, 1, tzinfo=timezone.utc)
COMMON_SOURCE_DISCOVERY_END = datetime(2020, 12, 31, tzinfo=timezone.utc)
COMMON_SOURCE_WARMUP_START = COMMON_SOURCE_DISCOVERY_START - timedelta(
    days=base.v31.WARMUP_DAYS
)
COMMON_SOURCE_WARMUP_END = base.robustness_warmup.v32.DATA_START - timedelta(days=1)
_COMMON_QUARTERS = tuple(
    period
    for period in base.v31.DISCOVERY_PERIODS
    if period.start >= COMMON_SOURCE_DISCOVERY_START
    and period.end <= COMMON_SOURCE_DISCOVERY_END
)
COMMON_SOURCE_DISCOVERY_PERIODS = (
    base.v31.Period(
        "2020-Q3-common-lag-safe",
        datetime(2020, 7, 2, tzinfo=timezone.utc),
        _COMMON_QUARTERS[0].end,
    ),
    _COMMON_QUARTERS[1],
)
_ORIGINAL_BUILD_REPORT = base.build_report
_OBSERVED_REPORT_SHA: dict[str, str] = {}
_WARMUP_INVENTORY: list[dict[str, Any]] = []


if not PROTOCOL_PATH.is_file():
    raise base.DualSourceConsensusV63Error(
        "v6.3.2 semantic dependency protocol is missing"
    )
if not COMMON_SOURCE_ADDENDUM_PATH.is_file():
    raise base.DualSourceConsensusV63Error(
        "v6.3.3 common-source discovery addendum is missing"
    )
if not SIGNAL_LAG_ADDENDUM_PATH.is_file():
    raise base.DualSourceConsensusV63Error(
        "v6.3.4 common-source signal-lag addendum is missing"
    )
if not WARMUP_ADDENDUM_PATH.is_file():
    raise base.DualSourceConsensusV63Error(
        "v6.3.5 common-source 200-day warmup addendum is missing"
    )
if tuple(period.name for period in _COMMON_QUARTERS) != (
    "2020-Q3",
    "2020-Q4",
):
    raise base.DualSourceConsensusV63Error(
        "common-source discovery quarters changed"
    )
if tuple(period.name for period in COMMON_SOURCE_DISCOVERY_PERIODS) != (
    "2020-Q3-common-lag-safe",
    "2020-Q4",
):
    raise base.DualSourceConsensusV63Error(
        "lag-safe common-source periods changed"
    )
if COMMON_SOURCE_WARMUP_START != datetime(2019, 12, 14, tzinfo=timezone.utc):
    raise base.DualSourceConsensusV63Error("common-source warmup start changed")


def _normalized_bar_payload(asset: str, bar: Any) -> dict[str, Any]:
    return {
        "asset": asset,
        "date": bar.hour.date().isoformat(),
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "quote_volume": float(bar.quote_volume),
        "taker_buy_quote_volume": float(bar.taker_buy_quote_volume),
    }


def _load_coinbase_with_common_source_warmup():
    global _WARMUP_INVENTORY
    v32 = base.robustness_warmup.v32
    bars, _ = v32.download_coinbase_bars()
    inventory: list[dict[str, Any]] = []
    for asset in v32.ASSETS:
        product = v32.PRODUCTS[asset]
        for start, end in v32._request_ranges(
            COMMON_SOURCE_WARMUP_START,
            COMMON_SOURCE_WARMUP_END,
        ):
            url = v32._candle_url(product, start, end)
            content, raw_digest = v32._download_json(url)
            parsed = v32._parse_coinbase_candles(
                content,
                asset=asset,
                requested_start=start,
                requested_end=end,
            )
            expected = set(v32._days(start, end))
            if set(parsed) != expected:
                missing = sorted(expected - set(parsed))
                raise base.DualSourceConsensusV63Error(
                    f"Coinbase {asset} discovery warmup incomplete; "
                    f"missing={len(missing)} first={missing[0].date() if missing else 'unknown'}"
                )
            for day, bar in parsed.items():
                if day in bars[asset] and bars[asset][day] != bar:
                    raise base.DualSourceConsensusV63Error(
                        f"Coinbase {asset} discovery warmup conflicts at {day.date()}"
                    )
                bars[asset][day] = bar
            normalized = [
                _normalized_bar_payload(asset, parsed[day])
                for day in sorted(parsed)
            ]
            inventory.append(
                {
                    "key": (
                        f"coinbase-discovery-warmup:{asset}:"
                        f"{start.date()}:{end.date()}"
                    ),
                    "provider": "coinbase-exchange-public-rest",
                    "product": product,
                    "requested_start": start.date().isoformat(),
                    "requested_end": end.date().isoformat(),
                    "url": url,
                    "raw_sha256": raw_digest,
                    "normalized_sha256": hashlib.sha256(
                        canonical_json(normalized).encode("utf-8")
                    ).hexdigest(),
                    "rows": len(normalized),
                }
            )

    dates = v32._days(COMMON_SOURCE_WARMUP_START, v32.EXIT_DATE)
    for asset in v32.ASSETS:
        missing = [day for day in dates if day not in bars[asset]]
        if missing:
            raise base.DualSourceConsensusV63Error(
                f"Coinbase {asset} extended history missing {len(missing)} days; "
                f"first={missing[0].date()}"
            )
    normalized_cash, _ = (
        base.robustness_warmup.cash_transport.download_cash_series_with_resilience()
    )
    features = base.v31.build_features(bars, dates)
    rates = base.robustness_warmup.cash_transport.parse_fred_rates(normalized_cash)
    cash_returns = base.v31.build_daily_cash_returns(rates, dates)
    required_feature_day = COMMON_SOURCE_DISCOVERY_START
    if required_feature_day not in features:
        raise base.DualSourceConsensusV63Error(
            "Coinbase common-source warmup still cannot construct 2020-07-01 features"
        )
    _WARMUP_INVENTORY = sorted(inventory, key=lambda item: item["key"])
    return bars, features, cash_returns


def semantic_projection(report: Mapping[str, Any]) -> dict[str, Any]:
    rank = report.get("rank_stability")
    sources = report.get("sources")
    if not isinstance(rank, Mapping) or not isinstance(sources, Mapping):
        raise base.DualSourceConsensusV63Error(
            "dependency lacks semantic evidence fields"
        )
    source_series: dict[str, str] = {}
    for source, payload in sorted(sources.items()):
        if not isinstance(payload, Mapping):
            raise base.DualSourceConsensusV63Error(
                f"dependency source payload is invalid: {source}"
            )
        digest = str(payload.get("standard_relative_series_sha256", ""))
        if len(digest) != 64:
            raise base.DualSourceConsensusV63Error(
                f"dependency source series fingerprint is invalid: {source}"
            )
        source_series[str(source)] = digest
    rank_summary = {
        key: value
        for key, value in rank.items()
        if key != "percentile_ranks"
    }
    return {
        "schema_version": report.get("schema_version"),
        "paper_only": report.get("paper_only"),
        "authorizes_trading": report.get("authorizes_trading"),
        "authorizes_continuous_paper": report.get(
            "authorizes_continuous_paper"
        ),
        "retrospective_dates_exposed": report.get(
            "retrospective_dates_exposed"
        ),
        "untouched_historical_dates": report.get(
            "untouched_historical_dates"
        ),
        "status": report.get("status"),
        "member_count": report.get("member_count"),
        "members": report.get("members"),
        "conservative": report.get("conservative"),
        "material_gates": report.get("material_gates"),
        "statistical_gates": report.get("statistical_gates"),
        "source_standard_relative_series_sha256": source_series,
        "deflated_sharpe": report.get("deflated_sharpe"),
        "rank_stability_summary": rank_summary,
    }


def semantic_fingerprint(report: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json(semantic_projection(report)).encode("utf-8")
    ).hexdigest()


def validate_dependency(
    report: Mapping[str, Any],
    *,
    expected_sha: str,
    expected_status: str,
    name: str,
) -> str:
    del expected_sha
    payload = dict(report)
    claimed = str(payload.pop("report_sha256", ""))
    computed = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    if claimed != computed:
        raise base.DualSourceConsensusV63Error(
            f"{name} whole-report self-hash does not match contents"
        )
    if report.get("paper_only") is not True:
        raise base.DualSourceConsensusV63Error(f"{name} is not paper-only")
    if report.get("authorizes_trading") is not False:
        raise base.DualSourceConsensusV63Error(f"{name} authorizes trading")
    frozen_status = EXPECTED_STATUS.get(name)
    if frozen_status is None or expected_status != frozen_status:
        raise base.DualSourceConsensusV63Error(
            f"{name} caller status contract changed"
        )
    if report.get("status") != frozen_status:
        raise base.DualSourceConsensusV63Error(f"{name} status changed")
    semantic = semantic_fingerprint(report)
    expected_semantic = EXPECTED_SEMANTIC.get(name)
    if semantic != expected_semantic:
        raise base.DualSourceConsensusV63Error(
            f"{name} semantic evidence changed: {semantic} != {expected_semantic}"
        )
    _OBSERVED_REPORT_SHA[name] = claimed
    return semantic


def build_report(*args: Any, **kwargs: Any) -> dict[str, Any]:
    original_validator = base._validate_dependency
    original_discovery_periods = base.v31.DISCOVERY_PERIODS
    original_coinbase_loader = (
        base.robustness_warmup._load_coinbase_with_real_warmup
    )
    base._validate_dependency = validate_dependency
    base.v31.DISCOVERY_PERIODS = COMMON_SOURCE_DISCOVERY_PERIODS
    base.robustness_warmup._load_coinbase_with_real_warmup = (
        _load_coinbase_with_common_source_warmup
    )
    _OBSERVED_REPORT_SHA.clear()
    _WARMUP_INVENTORY.clear()
    try:
        report = _ORIGINAL_BUILD_REPORT(*args, **kwargs)
    finally:
        base.robustness_warmup._load_coinbase_with_real_warmup = (
            original_coinbase_loader
        )
        base.v31.DISCOVERY_PERIODS = original_discovery_periods
        base._validate_dependency = original_validator
    report.pop("report_sha256", None)
    report["schema_version"] = "6.3.5-dual-source-common-discovery-200d-warmup"
    report["semantic_dependency_protocol_sha256"] = hashlib.sha256(
        PROTOCOL_PATH.read_bytes()
    ).hexdigest()
    report["common_source_discovery_addendum_sha256"] = hashlib.sha256(
        COMMON_SOURCE_ADDENDUM_PATH.read_bytes()
    ).hexdigest()
    report["common_source_signal_lag_addendum_sha256"] = hashlib.sha256(
        SIGNAL_LAG_ADDENDUM_PATH.read_bytes()
    ).hexdigest()
    report["common_source_200d_warmup_addendum_sha256"] = hashlib.sha256(
        WARMUP_ADDENDUM_PATH.read_bytes()
    ).hexdigest()
    report["common_source_discovery_periods"] = [
        {
            "name": period.name,
            "start": period.start.isoformat(),
            "end": period.end.isoformat(),
        }
        for period in COMMON_SOURCE_DISCOVERY_PERIODS
    ]
    report["coinbase_common_source_warmup"] = {
        "start": COMMON_SOURCE_WARMUP_START.isoformat(),
        "end": COMMON_SOURCE_WARMUP_END.isoformat(),
        "evaluation_rows": 0,
        "inventory": list(_WARMUP_INVENTORY),
    }
    report["dependency_evidence"] = {
        name: {
            "semantic_fingerprint": EXPECTED_SEMANTIC[name],
            "observed_whole_report_sha256": _OBSERVED_REPORT_SHA[name],
            "artifact_whole_report_sha256": ARTIFACT_REPORT_SHA[name],
            "status": EXPECTED_STATUS[name],
        }
        for name in sorted(EXPECTED_SEMANTIC)
    }
    report["report_sha256"] = hashlib.sha256(
        canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    original_builder = base.build_report
    base.build_report = build_report
    try:
        return base.main(argv)
    finally:
        base.build_report = original_builder


if __name__ == "__main__":
    raise SystemExit(main())
