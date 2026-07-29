from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from tradebot.reports.report_generator import to_json
from tradebot.scanner.crypto_scanner import ScannerConfig, scan_crypto_folder


def write_csv(path: Path, prices: list[float], volumes: list[float]) -> None:
    rows = ["timestamp,open,high,low,close,volume"]
    start = datetime(2025, 1, 1)
    for index, close in enumerate(prices):
        open_price = prices[index - 1] if index else close
        high = max(open_price, close) * 1.01
        low = min(open_price, close) * 0.99
        rows.append(f"{(start + timedelta(days=index)).isoformat()},{open_price},{high},{low},{close},{volumes[index]}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def trending_prices(start: float = 100.0, step: float = 1.0, count: int = 45) -> list[float]:
    return [start + step * index for index in range(count)]


def test_high_quality_coin_ranks_above_weak_coin(tmp_path):
    write_csv(tmp_path / "STRONG.csv", trending_prices(step=1.4), [5000 + i * 250 for i in range(45)])
    write_csv(tmp_path / "WEAK.csv", [100 + (i % 2) * 0.05 for i in range(45)], [3000 for _ in range(45)])
    results = scan_crypto_folder(tmp_path)
    assert results[0].symbol == "STRONG"
    assert results[0].opportunity_score > results[1].opportunity_score


def test_low_volume_coin_is_rejected(tmp_path):
    write_csv(tmp_path / "LOWVOL.csv", trending_prices(), [10 for _ in range(45)])
    result = scan_crypto_folder(tmp_path)[0]
    assert result.rejected
    assert result.rejection_reason == "low_liquidity"


def test_extreme_volatility_coin_is_rejected(tmp_path):
    prices = [100 if i % 2 == 0 else 170 for i in range(45)]
    write_csv(tmp_path / "WILD.csv", prices, [5000 for _ in range(45)])
    result = scan_crypto_folder(tmp_path)[0]
    assert result.rejected
    assert result.rejection_reason == "extreme_volatility"


def test_after_tax_weak_setup_is_rejected(tmp_path):
    write_csv(tmp_path / "WEAKNET.csv", trending_prices(step=0.2), [5000 for _ in range(45)])
    result = scan_crypto_folder(tmp_path, config=ScannerConfig(min_expected_net_percent=99.0))[0]
    assert result.rejected
    assert result.rejection_reason == "weak_after_cost_tax_profit"


def test_json_report_output_shape(tmp_path):
    write_csv(tmp_path / "STRONG.csv", trending_prices(step=1.4), [5000 + i * 250 for i in range(45)])
    results = scan_crypto_folder(tmp_path, top=1)
    payload = json.loads(to_json(results))
    assert payload[0]["rank"] == 1
    assert payload[0]["symbol"] == "STRONG"
    assert "opportunity_score" in payload[0]
    assert "estimated_net_profit_after_cost_tax" in payload[0]
    assert "rejection_reason" in payload[0]
