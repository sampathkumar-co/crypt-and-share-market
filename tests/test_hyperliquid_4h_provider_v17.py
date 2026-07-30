from __future__ import annotations

from datetime import timezone

import pytest

from tradebot.data import hyperliquid_4h_provider_v17 as provider


def rows() -> list[dict[str, object]]:
    output = []
    for index, stamp in enumerate(provider._expected_timestamps()):
        close = 100.0 + index * 0.01
        output.append(
            {
                "t": int(stamp.replace(tzinfo=timezone.utc).timestamp() * 1000),
                "T": int(
                    (stamp + provider.FOUR_HOURS).replace(tzinfo=timezone.utc).timestamp()
                    * 1000
                )
                - 1,
                "o": f"{close - 0.2}",
                "h": f"{close + 0.5}",
                "l": f"{close - 0.5}",
                "c": f"{close}",
                "v": "123.45",
                "i": "4h",
                "s": "APT",
                "n": 10,
            }
        )
    return output


def test_expected_grid_is_exact() -> None:
    expected = provider._expected_timestamps()
    assert len(expected) == provider.EXPECTED_ROWS == 3504
    assert expected[0] == provider.EXPECTED_START
    assert expected[-1] == provider.EXPECTED_END_EXCLUSIVE - provider.FOUR_HOURS


def test_parse_rows_requires_complete_valid_grid() -> None:
    raw, normalized = provider._parse_rows("APTUSDT", rows())
    assert len(raw) == provider.EXPECTED_ROWS
    assert len(normalized) == provider.EXPECTED_ROWS
    assert normalized[0]["timestamp"] == provider.EXPECTED_START.isoformat()
    assert normalized[-1]["timestamp"] == (
        provider.EXPECTED_END_EXCLUSIVE - provider.FOUR_HOURS
    ).isoformat()


def test_missing_timestamp_is_rejected() -> None:
    incomplete = rows()[1:]
    with pytest.raises(provider.HyperliquidPriceDataError, match="missing 1"):
        provider._parse_rows("APTUSDT", incomplete)


def test_duplicate_timestamp_is_rejected() -> None:
    duplicated = rows()
    duplicated.append(dict(duplicated[-1]))
    with pytest.raises(provider.HyperliquidPriceDataError, match="Duplicate"):
        provider._parse_rows("APTUSDT", duplicated)


def test_invalid_ohlcv_is_rejected() -> None:
    invalid = rows()
    invalid[10] = {**invalid[10], "h": "1", "l": "200"}
    with pytest.raises(provider.HyperliquidPriceDataError, match="Inconsistent"):
        provider._parse_rows("APTUSDT", invalid)


def test_fetch_and_verify_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    payload = rows()
    monkeypatch.setattr(provider, "_post_json", lambda body: payload)
    manifest = provider.fetch_hyperliquid_4h_bundle(tmp_path)
    assert len(manifest.sources) == 8
    assert manifest.expected_rows_per_asset == 3504
    assert manifest.dataset_fingerprint
    verified = provider.verify_hyperliquid_4h_manifest(tmp_path)
    assert verified["dataset_fingerprint"] == manifest.dataset_fingerprint
    assert {item["symbol"] for item in verified["sources"]} == set(
        provider.SYMBOL_TO_COIN
    )
