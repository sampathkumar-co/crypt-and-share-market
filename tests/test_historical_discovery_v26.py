from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tradebot.research import historical_discovery_v26 as v26


def _bar(timestamp: datetime, price: float, *, volume: float = 100.0, buy: float = 60.0) -> v26.FiveMinuteBar:
    return v26.FiveMinuteBar(
        timestamp=timestamp,
        open=price,
        high=price * 1.002,
        low=price * 0.998,
        close=price * 1.001,
        quote_volume=volume,
        taker_buy_quote_volume=buy,
    )


def _hour_state(hour: datetime, open_price: float, close_price: float | None = None) -> v26.HourState:
    close = open_price if close_price is None else close_price
    high = max(open_price, close) * 1.001
    low = min(open_price, close) * 0.999
    return v26.HourState(
        hour=hour,
        open=open_price,
        high=high,
        low=low,
        close=close,
        quote_volume=1_000_000.0,
        taker_imbalance=0.20,
        trend_efficiency=0.70,
        close_location=0.80,
        maximum_volume_share=0.20,
        late_early_volume_ratio=1.30,
        realized_volatility=0.01,
        range_fraction=(high - low) / close,
    )


def _asset_state(hour: datetime, price: float = 100.0) -> v26.AssetState:
    spot = _hour_state(hour, price, price * 1.001)
    perp = _hour_state(hour, price * 1.0005, price * 1.0015)
    return v26.AssetState(
        spot=spot,
        perp=perp,
        funding=-0.00001,
        open_interest=1_000_000.0,
        basis_bps=(perp.close / spot.close - 1.0) * 10_000.0,
        flow_lead=0.10,
    )


def test_aggregate_hour_requires_twelve_ordered_five_minute_bars() -> None:
    hour = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = [_bar(hour + timedelta(minutes=5 * index), 100.0 + index * 0.1) for index in range(12)]
    state = v26._aggregate_hour(hour, bars)
    assert state.open == pytest.approx(100.0)
    assert state.close == pytest.approx((101.1) * 1.001)
    assert state.quote_volume == pytest.approx(1_200.0)
    assert state.taker_imbalance == pytest.approx(0.2)
    assert 0.0 <= state.trend_efficiency <= 1.0
    assert 0.0 <= state.close_location <= 1.0
    with pytest.raises(v26.HistoricalDiscoveryV26Error):
        v26._aggregate_hour(hour, bars[:-1])


def test_archive_contract_uses_only_public_five_minute_inputs() -> None:
    requests = v26._archive_requests()
    assert requests
    assert all(url.startswith("https://data.binance.vision/data/") for url in requests.values())
    kline_urls = [url for key, url in requests.items() if key.startswith(("spot:", "futures:"))]
    assert kline_urls
    assert all("/5m/" in url for url in kline_urls)
    assert all("/1h/" not in url for url in kline_urls)
    assert any("/daily/metrics/" in url for url in requests.values())


def test_fixed_windows_and_safety_boundary_are_frozen() -> None:
    assert [(window.name, window.phase) for window in v26.WINDOWS] == [
        ("2025-08", "discovery"),
        ("2025-11", "discovery"),
        ("2026-02", "validation"),
        ("2026-05", "validation"),
    ]
    assert v26.WEIGHT == pytest.approx(0.15)
    assert v26.MIN_AMPLITUDE == pytest.approx(0.01)
    assert v26.STANDARD_COST == pytest.approx(0.002)
    assert v26.STRESS_COST == pytest.approx(0.004)
    assert v26.MODE == "HISTORICAL_COST_AWARE_DISCOVERY_ONLY"


def test_build_events_enforces_confirmation_delay_cooldown_and_no_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    screen_start = datetime(2026, 1, 10, tzinfo=timezone.utc)
    window = v26.WindowSpec(
        "test",
        "discovery",
        screen_start,
        screen_start + timedelta(hours=20),
    )
    states: dict[datetime, dict[str, v26.AssetState]] = {}
    first = screen_start - timedelta(hours=192)
    for offset in range(214):
        hour = first + timedelta(hours=offset)
        states[hour] = {asset: _asset_state(hour, 100.0 + offset * 0.01) for asset in v26.ASSETS}

    def residual(
        spec: v26.WindowSpec,
        market: dict[datetime, dict[str, v26.AssetState]],
        asset: str,
        signal: datetime,
        confirmation: datetime,
    ) -> v26.Candidate | None:
        del spec, market
        if asset != "ETH":
            return None
        return v26.Candidate(
            signal,
            confirmation,
            asset,
            "confirmed_residual_continuation",
            10.0,
            0.02,
            f"event-{signal.isoformat()}",
        )

    monkeypatch.setattr(v26, "_candidate_residual", residual)
    monkeypatch.setattr(v26, "_candidate_unwind", lambda *args, **kwargs: None)
    monkeypatch.setattr(v26, "_candidate_sweep", lambda *args, **kwargs: None)

    events = v26.build_events(window, states)
    assert len(events) >= 2
    assert events[0].confirmation_hour == events[0].signal_hour + timedelta(hours=1)
    assert events[0].entry_hour == events[0].signal_hour + timedelta(hours=2)
    for previous, current in zip(events, events[1:], strict=False):
        assert current.signal_hour - previous.signal_hour >= timedelta(hours=8)
        assert current.entry_hour >= previous.entry_hour + timedelta(hours=4)
    assert all(event.weight == pytest.approx(0.15) for event in events)


def test_evaluation_fills_at_t_plus_two_open_and_applies_round_trip_cost() -> None:
    signal = datetime(2026, 1, 1, tzinfo=timezone.utc)
    entry = signal + timedelta(hours=2)
    exit_hour = entry + timedelta(hours=4)
    event = v26.Event(
        window="2025-08",
        phase="discovery",
        signal_hour=signal,
        confirmation_hour=signal + timedelta(hours=1),
        entry_hour=entry,
        asset="ETH",
        family="confirmed_residual_continuation",
        score=10.0,
        amplitude=0.02,
        weight=0.15,
        event_key="event-1",
    )
    spot: dict[str, dict[datetime, v26.HourState]] = {asset: {} for asset in v26.ASSETS}
    for asset in v26.ASSETS:
        spot[asset][entry] = _hour_state(entry, 100.0)
        spot[asset][exit_hour] = _hour_state(exit_hour, 100.0)
    spot["ETH"][exit_hour] = _hour_state(exit_hour, 102.5)

    result = v26.evaluate_events([event], spot, 4, 0.002)
    expected_raw = 0.025
    expected_net = 0.15 * (expected_raw - 0.002)
    assert result["accepted_event_count"] == 1
    assert result["events"][0]["entry_hour"] == "2026-01-01T02:00:00Z"
    assert result["events"][0]["raw_asset_return"] == pytest.approx(expected_raw)
    assert result["events"][0]["portfolio_net_return"] == pytest.approx(expected_net)
    assert result["net_compounded_return"] == pytest.approx(expected_net)


def test_report_source_keeps_track_a_and_execution_disabled() -> None:
    source = v26.Path(v26.__file__).read_text(encoding="utf-8")
    assert '"authorizes_trading": False' in source
    assert '"authorizes_shadow_paper": False' in source
    assert '"changes_track_a": False' in source
    assert '"cannot_replace_forward_evidence": True' in source
    assert "forward-data/v2" not in source
    lowered = source.lower()
    for forbidden in ("private_key", "api_secret", "place_order", "create_order", "withdraw"):
        assert forbidden not in lowered
