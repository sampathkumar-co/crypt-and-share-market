from pathlib import Path

from tradebot.models import Market, ScanResult
from tradebot.scanner.crypto_scanner import ScannerConfig, _scan


def scan_equity_folder(folder: str | Path, top: int | None = None, config: ScannerConfig | None = None) -> list[ScanResult]:
    return _scan(folder, Market.EQUITY, top=top, config=config)
