from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tradebot.backtest.portfolio_trader import CryptoPortfolioPaperTrader
from tradebot.backtest.robustness import evaluate_robustness
from tradebot.models import Market
from tradebot.reports.report_generator import to_json
from tradebot.scanner.crypto_scanner import scan_crypto_folder

FORBIDDEN_FIELDS = {"api_key", "secret", "wallet", "private_key", "order", "orders", "place_order"}
REPORTS_DIR = Path("reports")
PAPER_STATE_DIR = Path("paper_state")


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"Dashboard: http://{host}:{port}", flush=True)
    print("PAPER MODE ONLY - dashboard exposes no live trading endpoints.", flush=True)
    server.serve_forever()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "DualMarketAIBotDashboard/0.1"

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(dashboard_html())
            return
        if path == "/health":
            self._send_json({"status": "ok"})
            return
        if path == "/reports/scanner":
            self._send_json(read_first_report(["crypto_scan_ml.json", "crypto_scan.json", "crypto_scan_dashboard.json"]))
            return
        if path == "/reports/portfolio":
            self._send_json(read_first_report(["crypto_portfolio_ml.json", "crypto_portfolio.json", "crypto_portfolio_dashboard.json"]))
            return
        if path == "/reports/robustness":
            self._send_json(read_first_report(["crypto_robustness.json", "crypto_robustness_dashboard.json"]))
            return
        if path == "/reports/ml-comparison":
            self._send_json(read_first_report(["crypto_ml_comparison.json"]))
            return
        if path == "/paper-live/state":
            self._send_json(read_json_file(PAPER_STATE_DIR / "crypto_live.json"))
            return
        if path == "/paper-live/trades":
            state = read_json_file(PAPER_STATE_DIR / "crypto_live.json")
            trades = state.get("data", {}).get("trade_history", []) if state.get("exists") else []
            self._send_json({"exists": state.get("exists", False), "trades": trades})
            return
        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        payload = self._read_body()
        if contains_forbidden_fields(payload):
            self._send_json({"error": "Forbidden live-trading or credential field rejected."}, status=400)
            return
        if path == "/run/scan":
            folder = str(payload.get("folder", "data/crypto"))
            top = int(payload.get("top", 20))
            results = scan_crypto_folder(folder, top=top)
            output = REPORTS_DIR / "crypto_scan_dashboard.json"
            write_json(output, json.loads(to_json(results)))
            self._send_json({"message": "scan complete", "report": str(output), "results": json.loads(to_json(results))})
            return
        if path == "/run/portfolio":
            folder = str(payload.get("folder", "data/crypto"))
            cash = float(payload.get("cash", 100000.0))
            result = CryptoPortfolioPaperTrader(cash=cash).run_folder(folder)
            output = REPORTS_DIR / "crypto_portfolio_dashboard.json"
            write_json(output, json.loads(to_json(result)))
            self._send_json({"message": "portfolio complete", "report": str(output), "result": json.loads(to_json(result))})
            return
        if path == "/run/robustness":
            folder = str(payload.get("folder", "data/crypto"))
            cash = float(payload.get("cash", 100000.0))
            result = evaluate_robustness(folder, cash=cash)
            output = REPORTS_DIR / "crypto_robustness_dashboard.json"
            write_json(output, json.loads(to_json(result)))
            self._send_json({"message": "robustness complete", "report": str(output), "result": json.loads(to_json(result))})
            return
        self._send_json({"error": "Not found"}, status=404)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            return payload if isinstance(payload, dict) else {"payload": payload}
        except json.JSONDecodeError:
            return {}

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        response = with_warning(payload)
        body = json.dumps(response, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def with_warning(payload: dict[str, Any]) -> dict[str, Any]:
    return {"paper_only": True, "warning": "PAPER MODE ONLY. No live trading, wallets, order endpoints, leverage, futures, or API keys.", **payload}


def contains_forbidden_fields(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in FORBIDDEN_FIELDS:
                return True
            if contains_forbidden_fields(item):
                return True
    elif isinstance(value, list):
        return any(contains_forbidden_fields(item) for item in value)
    return False


def read_first_report(names: list[str]) -> dict[str, Any]:
    for name in names:
        result = read_json_file(REPORTS_DIR / name)
        if result["exists"]:
            return result
    return {"exists": False, "data": None, "message": "No report file found."}


def read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path), "data": None}
    try:
        return {"exists": True, "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
    except json.JSONDecodeError as exc:
        return {"exists": False, "path": str(path), "data": None, "error": str(exc)}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def dashboard_html() -> str:
    return """<!doctype html>
<html><head><meta charset='utf-8'><title>Dual Market AI Bot Dashboard</title>
<style>body{font-family:Arial;margin:24px;background:#0f172a;color:#e2e8f0} .warn{background:#7f1d1d;padding:16px;border-radius:8px;font-size:22px;font-weight:bold}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}.card{background:#1e293b;padding:16px;border-radius:8px}pre{white-space:pre-wrap;max-height:360px;overflow:auto}</style></head>
<body><div class='warn'>PAPER MODE ONLY — no live trading, wallets, order endpoints, leverage, futures, API keys, or guaranteed profit.</div>
<h1>Dual Market AI Bot</h1><div class='grid'>
<div class='card'><h2>Scanner</h2><pre id='scanner'>Loading...</pre></div>
<div class='card'><h2>Portfolio</h2><pre id='portfolio'>Loading...</pre></div>
<div class='card'><h2>Robustness</h2><pre id='robustness'>Loading...</pre></div>
<div class='card'><h2>ML Comparison</h2><pre id='ml'>Loading...</pre></div>
<div class='card'><h2>Paper-live State</h2><pre id='state'>Loading...</pre></div>
<div class='card'><h2>Paper-live Trades</h2><pre id='trades'>Loading...</pre></div>
</div><script>
async function load(id,url){try{const r=await fetch(url);document.getElementById(id).textContent=JSON.stringify(await r.json(),null,2)}catch(e){document.getElementById(id).textContent=e}}
load('scanner','/reports/scanner');load('portfolio','/reports/portfolio');load('robustness','/reports/robustness');load('ml','/reports/ml-comparison');load('state','/paper-live/state');load('trades','/paper-live/trades');
</script></body></html>"""
