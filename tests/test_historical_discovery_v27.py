from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tradebot.research import historical_discovery_v26 as v26
from tradebot.research import historical_discovery_v27 as v27


def hour_state(
    hour: datetime,
    *,
    close: float = 100.0,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    quote_volume: float = 100.0,
    imbalance: float = 0.0,
    efficiency: float = 0.5,
    close_location: float = 0.5,
    volatility: float = 0.01,
    range_fraction: float = 0.02,
) -> v26.HourState:
    open_value = close if open_ is None else open_
    high_value = max(close, open_value) * 1.002 if high is None else high
    low_value = min(close, open_value) * 0.998 if low is None else low
    return v26.HourState(
        hour=hour,
        open=open_value,
        high=high_value,
        low=low_value,
        close=close,
        quote_volume=quote_volume,
        taker_imbalance=imbalance,
        trend_efficiency=efficiency,
        close_location=close_location,
        maximum_volume_share=0.10,
        late_early_volume_ratio=1.0,
        realized_volatility=volatility,
        range_fraction=range_fraction,
    )


def asset_state(
    hour: datetime,
    *,
    close: float = 100.0,
    funding: float = 0.00005,
    oi: float = 1_000.0,
    basis: float = 0.0,
    flow: float = 0.0,
    **kwargs: float,
) -> v26.AssetState:
    spot = hour_state(hour, close=close, **kwargs)
    perp = hour_state(hour, close=close * (1.0 + basis / 10_000.0), **kwargs)
    return v26.AssetState(
        spot=spot,
        perp=perp,
        funding=funding,
        open_interest=oi,
        basis_bps=basis,
        flow_lead=flow,
    )


def test_protocol_requires_five_validation_windows() -> None:
    assert len(v27.VALIDATION_WINDOWS) == 5
    assert len(set(v27.VALIDATION_WINDOWS)) == 5
    assert v27.PRIMARY_HORIZON == 8
    assert v27.WEIGHT == pytest.approx(0.15)
    assert v27.STANDARD_COST == pytest.approx(0.002)
    assert v27.STRESS_COST == pytest.approx(0.004)


def test_pullback_candidate_requires_reclaim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(v26, "_beta", lambda *_args, **_kwargs: 1.0)
    signal = datetime(2026, 1, 10, tzinfo=timezone.utc)
    confirmation = signal + timedelta(hours=1)
    states: dict[datetime, dict[str, v26.AssetState]] = {}

    def put(hour: datetime, asset: str, state: v26.AssetState) -> None:
        states.setdefault(hour, {})[asset] = state

    put(signal - timedelta(hours=72), "SOL", asset_state(signal - timedelta(hours=72), close=100.0))
    put(signal - timedelta(hours=72), "BTC", asset_state(signal - timedelta(hours=72), close=100.0))
    put(signal - timedelta(hours=24), "SOL", asset_state(signal - timedelta(hours=24), close=106.0, oi=1_000.0))
    put(signal - timedelta(hours=3), "SOL", asset_state(signal - timedelta(hours=3), close=110.0))
    put(signal - timedelta(hours=3), "BTC", asset_state(signal - timedelta(hours=3), close=101.8))
    put(signal - timedelta(hours=1), "SOL", asset_state(signal - timedelta(hours=1), close=109.0))
    put(
        signal,
        "SOL",
        asset_state(
            signal,
            close=108.0,
            open_=109.0,
            high=109.2,
            low=106.5,
            funding=0.00005,
            oi=1_020.0,
            basis=5.0,
            flow=-0.02,
            imbalance=-0.05,
            efficiency=0.35,
            close_location=0.55,
        ),
    )
    put(signal, "BTC", asset_state(signal, close=102.0))
    put(
        confirmation,
        "SOL",
        asset_state(
            confirmation,
            close=108.6,
            open_=108.0,
            high=108.8,
            low=107.8,
            oi=1_022.0,
            basis=4.0,
            flow=0.10,
            imbalance=0.20,
            efficiency=0.60,
            close_location=0.80,
        ),
    )
    put(confirmation, "BTC", asset_state(confirmation, close=102.1))

    candidate = v27._candidate_pullback(
        v27.WINDOWS[0], states, "SOL", signal, confirmation
    )
    assert candidate is not None
    assert candidate.family == "trend_pullback_reclaim"
    assert candidate.confirmation_hour == confirmation


def test_capitulation_candidate_requires_stabilization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(v26, "_beta", lambda *_args, **_kwargs: 1.0)
    signal = datetime(2026, 1, 10, tzinfo=timezone.utc)
    confirmation = signal + timedelta(hours=1)
    states: dict[datetime, dict[str, v26.AssetState]] = {}
    for offset in range(168, 0, -1):
        hour = signal - timedelta(hours=offset)
        states[hour] = {
            "SOL": asset_state(hour, close=100.0, funding=0.00010, basis=5.0, oi=1_000.0),
            "BTC": asset_state(hour, close=100.0),
        }
    states[signal - timedelta(hours=6)]["SOL"] = asset_state(
        signal - timedelta(hours=6), close=100.0, oi=1_000.0
    )
    states[signal - timedelta(hours=6)]["BTC"] = asset_state(
        signal - timedelta(hours=6), close=100.0
    )
    states[signal] = {
        "SOL": asset_state(
            signal,
            close=95.0,
            open_=97.0,
            high=97.2,
            low=94.5,
            funding=-0.00050,
            oi=940.0,
            basis=-20.0,
            flow=-0.10,
            imbalance=-0.20,
            efficiency=0.70,
            close_location=0.20,
        ),
        "BTC": asset_state(signal, close=99.0),
    }
    states[confirmation] = {
        "SOL": asset_state(
            confirmation,
            close=95.8,
            open_=95.0,
            high=96.0,
            low=94.9,
            funding=-0.00040,
            oi=935.0,
            basis=-15.0,
            flow=0.12,
            imbalance=0.20,
            efficiency=0.55,
            close_location=0.80,
        ),
        "BTC": asset_state(confirmation, close=99.1),
    }
    candidate = v27._candidate_capitulation(
        v27.WINDOWS[0], states, "SOL", signal, confirmation
    )
    assert candidate is not None
    assert candidate.family == "post_capitulation_recovery"
    assert candidate.diagnostics["oi6"] < -0.03


def test_compression_breakout_candidate_holds_level() -> None:
    signal = datetime(2026, 1, 10, tzinfo=timezone.utc)
    confirmation = signal + timedelta(hours=1)
    states: dict[datetime, dict[str, v26.AssetState]] = {}
    for offset in range(168, 0, -1):
        hour = signal - timedelta(hours=offset)
        compressed = offset <= 12
        states[hour] = {
            "ETH": asset_state(
                hour,
                close=99.8,
                high=100.0 if compressed else 101.0,
                low=99.6 if compressed else 98.0,
                quote_volume=100.0,
                volatility=0.001 if compressed else 0.010,
                range_fraction=0.004 if compressed else 0.020,
            ),
            "BTC": asset_state(hour, close=100.0),
        }
    states[signal - timedelta(hours=48)]["BTC"] = asset_state(
        signal - timedelta(hours=48), close=100.0
    )
    states[signal] = {
        "ETH": asset_state(
            signal,
            close=100.3,
            open_=99.9,
            high=100.5,
            low=99.8,
            quote_volume=160.0,
            imbalance=0.20,
            efficiency=0.70,
            close_location=0.82,
            funding=0.00005,
            basis=5.0,
            flow=0.12,
            volatility=0.006,
            range_fraction=0.007,
        ),
        "BTC": asset_state(signal, close=100.5),
    }
    states[confirmation] = {
        "ETH": asset_state(
            confirmation,
            close=100.4,
            open_=100.3,
            high=100.6,
            low=100.1,
            imbalance=0.10,
            efficiency=0.45,
            close_location=0.70,
            flow=0.02,
            volatility=0.004,
            range_fraction=0.005,
        ),
        "BTC": asset_state(confirmation, close=100.5),
    }
    candidate = v27._candidate_breakout(
        v27.WINDOWS[0], states, "ETH", signal, confirmation
    )
    assert candidate is not None
    assert candidate.family == "compression_breakout_hold"
    assert candidate.diagnostics["hold_fraction"] >= 0.0


def test_build_events_fills_at_t_plus_2_and_respects_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime(2024, 7, 1, tzinfo=timezone.utc)
    window = v27.WindowSpec("unit", "discovery", start, start + timedelta(hours=30))
    states: dict[datetime, dict[str, v26.AssetState]] = {}
    for offset in range(-168, 33):
        hour = start + timedelta(hours=offset)
        states[hour] = {asset: asset_state(hour) for asset in v27.ASSETS}

    def always_candidate(
        _window: v27.WindowSpec,
        _states: dict[datetime, dict[str, v26.AssetState]],
        asset: str,
        signal: datetime,
        confirmation: datetime,
    ) -> v27.Candidate | None:
        if asset != "ETH":
            return None
        diagnostics = {"value": 1.0}
        return v27.Candidate(
            signal,
            confirmation,
            asset,
            "unit_family",
            1.0,
            0.01,
            diagnostics,
            f"event-{signal.isoformat()}",
        )

    monkeypatch.setattr(v27, "BUILDERS", (always_candidate,))
    events = v27.build_events(window, states)
    assert events
    assert events[0].entry_hour == events[0].signal_hour + timedelta(hours=2)
    assert all(
        later.signal_hour - earlier.signal_hour >= timedelta(hours=v27.COOLDOWN_HOURS)
        for earlier, later in zip(events, events[1:], strict=False)
    )


def test_evaluation_charges_weighted_round_trip_cost() -> None:
    entry_hour = datetime(2024, 7, 2, tzinfo=timezone.utc)
    exit_hour = entry_hour + timedelta(hours=8)
    spot: dict[str, dict[datetime, v26.HourState]] = {asset: {} for asset in v27.ASSETS}
    for asset in v27.ASSETS:
        spot[asset][entry_hour] = hour_state(entry_hour, close=100.0, open_=100.0)
        spot[asset][exit_hour] = hour_state(exit_hour, close=101.0, open_=101.0)
    event = v27.Event(
        window=v27.WINDOWS[0].name,
        phase="discovery",
        signal_hour=entry_hour - timedelta(hours=2),
        confirmation_hour=entry_hour - timedelta(hours=1),
        entry_hour=entry_hour,
        asset="ETH",
        family="trend_pullback_reclaim",
        score=1.0,
        amplitude=0.01,
        diagnostics={},
        weight=0.15,
        event_key="unit-event",
    )
    result = v27.evaluate_events([event], spot, 8, 0.002)
    assert result["events"][0]["portfolio_net_return"] == pytest.approx(
        0.15 * (0.01 - 0.002)
    )
    assert result["window_event_counts"][v27.WINDOWS[0].name] == 1


def test_workflow_isolated_from_track_a() -> None:
    workflow = Path(".github/workflows/v27-fivefold-mechanism-discovery.yml").read_text(
        encoding="utf-8"
    )
    source = Path("src/tradebot/research/historical_discovery_v27.py").read_text(
        encoding="utf-8"
    )
    assert "historical-results/v27" in workflow
    assert "git push origin HEAD:historical-results/v27" in workflow
    assert "forward-data/v2" not in source
    assert "contents: write" in workflow
    assert "pull_request" in workflow
