from __future__ import annotations

import io
from urllib.error import HTTPError

import pytest

from tradebot.data import crypto_external_factors_hyperliquid_rate_limited as paced


class _Response:
    status = 200

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return b'[{"time":1,"fundingRate":"0.0"}]'


def test_retry_after_header_is_respected() -> None:
    error = HTTPError(
        "https://api.hyperliquid.xyz/info",
        429,
        "Too Many Requests",
        {"Retry-After": "7"},
        io.BytesIO(),
    )
    assert paced._retry_delay(error, 1) == 7.0


def test_429_without_header_uses_bounded_exponential_backoff() -> None:
    error = HTTPError(
        "https://api.hyperliquid.xyz/info",
        429,
        "Too Many Requests",
        {},
        io.BytesIO(),
    )
    assert paced._retry_delay(error, 1) == 15.0
    assert paced._retry_delay(error, 5) == 120.0


def test_paced_post_sleeps_before_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(paced.time, "sleep", sleeps.append)
    monkeypatch.setattr(paced, "urlopen", lambda request, timeout: _Response())

    payload = paced._rate_limited_post_json(
        {"type": "fundingHistory", "coin": "LTC", "startTime": 1},
        retries=1,
    )

    assert payload == [{"time": 1, "fundingRate": "0.0"}]
    assert sleeps == [paced.MIN_REQUEST_INTERVAL_SECONDS]
