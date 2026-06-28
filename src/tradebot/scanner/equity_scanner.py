from pathlib import Path
from tradebot.models import Market, ScanResult
from tradebot.scanner.crypto_scanner import _scan

def scan_equity_folder(folder: str | Path) -> list[ScanResult]:
    return _scan(folder, Market.EQUITY)
