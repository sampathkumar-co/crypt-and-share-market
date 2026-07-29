from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tradebot.backtest.portfolio_trader import CryptoPortfolioPaperTrader
from tradebot.backtest.robustness import evaluate_robustness
from tradebot.reports.report_generator import to_json
from tradebot.scanner.crypto_scanner import scan_crypto_folder

FORBIDDEN_FIELDS = {"api_key", "secret", "wallet", "private_key", "order", "orders", "place_order"}
REPORTS_DIR = Path("reports")
PAPER_STATE_DIR = Path("paper_state")
MAX_REQUEST_BYTES = 64 * 1024
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    if host not in LOOPBACK_HOSTS:
        raise ValueError("The research dashboard is local-only; bind it to 127.0.0.1, localhost, or ::1")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"Dashboard: http://{host}:{port}", flush=True)
    print("PAPER MODE ONLY - dashboard exposes no live trading endpoints.", flush=True)
    server.serve_forever()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "DualMarketAIBotDashboard/0.2"

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
        try:
            payload = self._read_body()
            if contains_forbidden_fields(payload):
                raise ValueError("Forbidden live-trading or credential field rejected.")

            if path == "/run/scan":
                folder = resolve_data_folder(payload.get("folder", "data/crypto"))
                top = bounded_int(payload.get("top", 20), "top", minimum=1, maximum=200)
                results = scan_crypto_folder(folder, top=top)
                output = REPORTS_DIR / "crypto_scan_dashboard.json"
                write_json(output, json.loads(to_json(results)))
                self._send_json({"message": "scan complete", "report": str(output), "results": json.loads(to_json(results))})
                return

            if path == "/run/portfolio":
                folder = resolve_data_folder(payload.get("folder", "data/crypto"))
                cash = bounded_float(payload.get("cash", 100000.0), "cash", minimum=1.0, maximum=1_000_000_000.0)
                result = CryptoPortfolioPaperTrader(cash=cash).run_folder(folder)
                output = REPORTS_DIR / "crypto_portfolio_dashboard.json"
                write_json(output, json.loads(to_json(result)))
                self._send_json({"message": "portfolio complete", "report": str(output), "result": json.loads(to_json(result))})
                return

            if path == "/run/robustness":
                folder = resolve_data_folder(payload.get("folder", "data/crypto"))
                cash = bounded_float(payload.get("cash", 100000.0), "cash", minimum=1.0, maximum=1_000_000_000.0)
                result = evaluate_robustness(folder, cash=cash)
                output = REPORTS_DIR / "crypto_robustness_dashboard.json"
                write_json(output, json.loads(to_json(result)))
                self._send_json({"message": "robustness complete", "report": str(output), "result": json.loads(to_json(result))})
                return

            self._send_json({"error": "Not found"}, status=404)
        except (TypeError, ValueError, OSError) as exc:
            self._send_json({"error": str(exc)}, status=400)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_body(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"", "application/json"}:
            raise ValueError("Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError(f"Request body must not exceed {MAX_REQUEST_BYTES} bytes")
        if length == 0:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON request body must be an object")
        return payload

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        response = with_warning(payload)
        body = json.dumps(response, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def with_warning(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "paper_only": True,
        "warning": "PAPER MODE ONLY. No live trading, wallets, order endpoints, leverage, futures, or API keys.",
        **payload,
    }


def contains_forbidden_fields(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in FORBIDDEN_FIELDS or any(token in normalized for token in ("private_key", "api_secret")):
                return True
            if contains_forbidden_fields(item):
                return True
    elif isinstance(value, list):
        return any(contains_forbidden_fields(item) for item in value)
    return False


def resolve_data_folder(value: Any) -> Path:
    requested = Path(str(value))
    candidate = (Path.cwd() / requested).resolve() if not requested.is_absolute() else requested.resolve()
    allowed_root = (Path.cwd() / "data").resolve()
    if candidate != allowed_root and allowed_root not in candidate.parents:
        raise ValueError("Dashboard folder must be inside the local data directory")
    if not candidate.exists() or not candidate.is_dir():
        raise ValueError(f"Data folder not found: {candidate}")
    return candidate


def bounded_int(value: Any, name: str, *, minimum: int, maximum: int) -> int:
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def bounded_float(value: Any, name: str, *, minimum: float, maximum: float) -> float:
    parsed = float(value)
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


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
    except (OSError, json.JSONDecodeError) as exc:
        return {"exists": False, "path": str(path), "data": None, "error": str(exc)}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def dashboard_html() -> str:
    return """<!doctype html>
<html><head><meta charset='utf-8'><title>Dual Market AI Bot Dashboard</title>
<style>body{font-family:Arial;margin:24px;background:#0f172a;color:#e2e8f0}.warn{background:#7f1d1d;padding:16px;border-radius:8px;font-size:22px;font-weight:bold}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}.card{background:#1e293b;padding:16px;border-radius:8px}pre{white-space:pre-wrap;max-height:360px;overflow:auto}</style></head>
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
