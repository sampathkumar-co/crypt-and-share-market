from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import numpy as np
import pytest

from tradebot.research import coinbase_execution_replication_v55 as v55


def portfolio(net_return: float, *, attenuated: int = 1):
    return {
        "net_return": net_return,
        "maximum_drawdown": 0.01,
        "target_changing_actions": 5,
        "attenuated_decision_count": attenuated,
        "never_added_asset": True,
        "never_increased_target": True,
    }


def simulation(excess: float = 0.001):
    baseline = portfolio(0.01, attenuated=0)
    candidate = portfolio(0.01 + excess)
    return {
        "standard": {
            "baseline": baseline,
            "candidate": candidate,
            "excess_return": excess,
        },
        "stress": {
            "baseline": baseline,
            "candidate": candidate,
            "excess_return": excess,
        },
    }


def manifest_payload():
    rows = []
    for index in range(30):
        stamp = v55.START + timedelta(days=index)
        rows.append({
            "date": stamp.date().isoformat(),
            "attenuated_rebalance": index == 3,
        })
    value = {
        "schema_version": v55.MANIFEST_SCHEMA_VERSION,
        "rows": rows,
    }
    value["manifest_sha256"] = hashlib.sha256(
        v55.canonical_json(value).encode("utf-8")
    ).hexdigest()
    return value
def test_products_are_frozen_and_complete() -> None:
    assert set(v55.PRODUCTS) == set(v55.ASSETS)
    assert v55.PRODUCTS["BTC"] == "BTC-USD"
    assert v55.PRODUCTS["ADA"] == "ADA-USD"


def test_required_execution_open_dates_are_exact() -> None:
    dates = v55.required_execution_open_dates()
    assert len(dates) == 31
    assert dates[0].date().isoformat() == "2026-07-02"
    assert dates[-1].date().isoformat() == "2026-08-01"


def test_validate_v542_report_accepts_exact_pass() -> None:
    report = {
        "schema_version": v55.v542.SCHEMA_VERSION,
        "report_sha256": v55.V542_REPORT_SHA256,
        "status": "FORWARD_TAIL_PASSED",
        "decision_dates": [str(index) for index in range(30)],
        "tail_dataset_integrity": {"exact": True},
        "attenuated_rebalance_dates": ["2026-07-04"],
    }
    v55.validate_v542_report(report)


def test_validate_manifest_uses_locked_hash(monkeypatch) -> None:
    manifest = manifest_payload()
    monkeypatch.setattr(
        v55, "EXPECTED_MANIFEST_SHA256", manifest["manifest_sha256"]
    )
    v55.validate_manifest(manifest)


def test_validate_manifest_rejects_modified_rows(monkeypatch) -> None:
    manifest = manifest_payload()
    monkeypatch.setattr(
        v55, "EXPECTED_MANIFEST_SHA256", manifest["manifest_sha256"]
    )
    manifest["rows"][0]["attenuated_rebalance"] = True
    with pytest.raises(v55.CoinbaseExecutionReplicationV55Error):
        v55.validate_manifest(manifest)
def fake_coinbase_downloader(url: str):
    rows = []
    stamp = v55.COINBASE_START
    index = 0
    while stamp <= v55.COINBASE_END:
        opened = 100.0 + index
        rows.append([
            int(stamp.timestamp()),
            opened * 0.99,
            opened * 1.01,
            opened,
            opened * 1.002,
            10.0,
        ])
        stamp += timedelta(days=1)
        index += 1
    content = json.dumps(rows).encode("utf-8")
    return content, hashlib.sha256(content).hexdigest()


def test_coinbase_download_reuses_parser_for_all_assets() -> None:
    opens, report = v55.download_coinbase_opens(
        downloader=fake_coinbase_downloader,
        sleeper=lambda _: None,
    )
    assert report["successful_asset_count"] == 5
    assert len(report["inventory"]) == 5
    assert all(len(opens[asset]) == 31 for asset in v55.ASSETS)
    assert opens["BTC"][v55.COINBASE_END] == pytest.approx(131.0)


def simple_dataset():
    dates = []
    assets = []
    for day in [v55.START - timedelta(days=1)] + [
        v55.START + timedelta(days=index) for index in range(30)
    ]:
        for asset in v55.ASSETS:
            dates.append(day)
            assets.append(asset)
    count = len(dates)
    return v55.Dataset(
        X=np.arange(count * 2, dtype=float).reshape(count, 2),
        return1=np.full(count, 0.123, dtype=float),
        return3=np.zeros(count),
        return7=np.zeros(count),
        rank3=np.zeros(count),
        meta=np.zeros(count, dtype=int),
        downside3=np.zeros(count, dtype=int),
        regimes=np.zeros(count, dtype=int),
        dates=dates,
        assets=assets,
        feature_names=["a", "b"],
    )


def simple_opens():
    result = {asset: {} for asset in v55.ASSETS}
    for asset_index, asset in enumerate(v55.ASSETS):
        for day_index, stamp in enumerate(v55.required_execution_open_dates()):
            result[asset][stamp] = 100.0 + asset_index + day_index
    return result
def test_replace_execution_returns_changes_only_july_return1() -> None:
    dataset = simple_dataset()
    replica, report = v55.replace_execution_returns(dataset, simple_opens())
    assert report["replaced_row_count"] == 150
    assert report["exact_except_july_return1"] is True
    assert np.array_equal(dataset.X, replica.X)
    outside = [index for index, stamp in enumerate(dataset.dates) if stamp < v55.START]
    assert np.array_equal(dataset.return1[outside], replica.return1[outside])
    index = list(zip(replica.dates, replica.assets, strict=True)).index(
        (v55.START, "BTC")
    )
    expected = 101.0 / 100.0 - 1.0
    assert replica.return1[index] == pytest.approx(expected)


def test_replace_execution_returns_rejects_missing_asset() -> None:
    opens = simple_opens()
    opens.pop("ADA")
    with pytest.raises(v55.CoinbaseExecutionReplicationV55Error):
        v55.replace_execution_returns(simple_dataset(), opens)


def test_replication_gates_pass_safe_positive_result() -> None:
    replacement = {
        "exact_except_july_return1": True,
        "replaced_row_count": 150,
    }
    gates = v55.replication_gates(
        simulation(),
        replacement,
        manifest_exact=True,
        source_complete=True,
    )
    assert all(gates.values())


def test_replication_status_transitions() -> None:
    assert v55.replication_status(
        source_complete=False,
        attenuated_decisions=1,
        gates=None,
    ) == "COINBASE_EXECUTION_DATA_INCONCLUSIVE"
    assert v55.replication_status(
        source_complete=True,
        attenuated_decisions=0,
        gates=None,
    ) == "COINBASE_EXECUTION_NO_SIGNAL"
    assert v55.replication_status(
        source_complete=True,
        attenuated_decisions=1,
        gates={"a": True},
    ) == "COINBASE_EXECUTION_REPLICATION_PASSED"
    assert v55.replication_status(
        source_complete=True,
        attenuated_decisions=1,
        gates={"a": False},
    ) == "COINBASE_EXECUTION_REPLICATION_FAILED"
