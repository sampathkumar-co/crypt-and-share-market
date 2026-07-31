from __future__ import annotations

from urllib.error import URLError

from tradebot.research import historical_yield_trend_v31 as v31
from tradebot.research import historical_yield_trend_v31_runner as runner


class FakeResponse:
    status = 200

    def __init__(self, content: bytes):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.content


def test_fred_transport_falls_back_only_after_bounded_primary_retries(
    monkeypatch,
) -> None:
    calls: list[str] = []
    content = b"observation_date,DGS3MO\n2017-08-31,1.00\n"

    def fake_urlopen(request, timeout):
        url = request.full_url
        calls.append(url)
        if url == v31.FRED_URL:
            raise URLError("temporary 502")
        assert url == runner.FRED_FALLBACK_URL
        return FakeResponse(content)

    monkeypatch.setattr(runner, "urlopen", fake_urlopen)
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)
    runner._reset_transport_audit()

    downloaded, inventory = runner.download_fred_with_retry(
        attempts_per_url=4,
        timeout=1.0,
    )

    assert downloaded == content
    assert calls == [v31.FRED_URL] * 4 + [runner.FRED_FALLBACK_URL]
    assert inventory["key"] == "cash:DGS3MO"
    assert inventory["url"] == runner.FRED_FALLBACK_URL
    assert inventory["sha256"]
    assert runner._FRED_TRANSPORT_AUDIT == {
        "attempt_count": 5,
        "attempted_urls": [v31.FRED_URL, runner.FRED_FALLBACK_URL],
        "selected_url": runner.FRED_FALLBACK_URL,
    }


def test_guard_restores_parser_and_downloader(monkeypatch) -> None:
    captured: dict[str, bool] = {}

    def fake_run(*, max_workers: int):
        captured["parser"] = (
            v31.parse_cash_rates is runner.parse_cash_rates_flexible
        )
        captured["downloader"] = (
            v31._download_fred is runner.download_fred_with_retry
        )
        return {
            "fingerprints": {
                "protocol_sha256": "a",
                "addendum_sha256": "b",
                "implementation_sha256": "c",
                "chosen_model_sha256": "d",
            },
            "report_sha256": "stale",
        }

    original_parser = v31.parse_cash_rates
    original_downloader = v31._download_fred
    monkeypatch.setattr(v31, "run_overlay", fake_run)
    report = runner.run_guarded_overlay(max_workers=2)

    assert captured == {"parser": True, "downloader": True}
    assert v31.parse_cash_rates is original_parser
    assert v31._download_fred is original_downloader
    assert report["cash_transport_policy"] == runner.CASH_TRANSPORT_POLICY
    assert report["cash_transport_audit"] == {
        "attempt_count": 0,
        "attempted_urls": [],
        "selected_url": None,
    }
    assert report["fingerprints"]["cash_transport_sha256"]
    assert report["report_sha256"] != "stale"
