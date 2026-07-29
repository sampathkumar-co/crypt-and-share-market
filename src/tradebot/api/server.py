from __future__ import annotations

import ipaddress
import json
import os
import secrets
import signal
import tempfile
import threading
import time
from dataclasses import dataclass
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
MIN_ADMIN_TOKEN_LENGTH = 32
DEFAULT_REQUEST_TIMEOUT = 20.0
STARTED_AT = time.monotonic()


class RequestError(ValueError):
    def __init__(self, status: int, message: str, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status = status
        self.headers = headers or {}


@dataclass(frozen=True)
class ServerConfig:
    data_dir: Path
    reports_dir: Path
    state_dir: Path
    public: bool = False
    enable_mutations: bool = True
    admin_token: str | None = None
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT


class TradebotHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(self, server_address: tuple[str, int], config: ServerConfig):
        self.tradebot_config = config
        super().__init__(server_address, DashboardHandler)

    def get_request(self):
        request, client_address = super().get_request()
        request.settimeout(self.tradebot_config.request_timeout)
        return request, client_address


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be one of true/false, 1/0, yes/no, or on/off")


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _configured_path(explicit: str | Path | None, env_name: str, fallback: str) -> Path:
    raw = explicit if explicit is not None else os.getenv(env_name, fallback)
    return Path(raw).expanduser().resolve()


def build_server_config(
    host: str,
    *,
    allow_public: bool | None = None,
    enable_mutations: bool | None = None,
    admin_token: str | None = None,
    data_dir: str | Path | None = None,
    reports_dir: str | Path | None = None,
    state_dir: str | Path | None = None,
    request_timeout: float | None = None,
) -> ServerConfig:
    public = not is_loopback_host(host)
    public_allowed = env_bool("TRADEBOT_ALLOW_PUBLIC", False) if allow_public is None else allow_public
    if public and not public_allowed:
        raise ValueError(
            "The research dashboard is local-only by default; use --public or TRADEBOT_ALLOW_PUBLIC=true "
            "to bind a non-loopback address"
        )

    if enable_mutations is None:
        default_mutations = not public
        mutations = env_bool("TRADEBOT_ENABLE_MUTATIONS", default_mutations)
    else:
        mutations = enable_mutations

    token = (admin_token if admin_token is not None else os.getenv("TRADEBOT_ADMIN_TOKEN", "")).strip() or None
    if public and mutations and (token is None or len(token) < MIN_ADMIN_TOKEN_LENGTH):
        raise ValueError(
            f"Public mutation endpoints require TRADEBOT_ADMIN_TOKEN with at least {MIN_ADMIN_TOKEN_LENGTH} characters"
        )

    timeout = request_timeout
    if timeout is None:
        timeout = float(os.getenv("TRADEBOT_REQUEST_TIMEOUT", str(DEFAULT_REQUEST_TIMEOUT)))
    if not 1.0 <= timeout <= 300.0:
        raise ValueError("request timeout must be between 1 and 300 seconds")

    configured_data = _configured_path(data_dir, "TRADEBOT_DATA_DIR", "data")
    configured_reports = _configured_path(reports_dir, "TRADEBOT_REPORTS_DIR", "reports")
    configured_state = _configured_path(state_dir, "TRADEBOT_STATE_DIR", "paper_state")

    if not configured_data.exists() or not configured_data.is_dir():
        raise ValueError(f"Data directory not found: {configured_data}")
    configured_reports.mkdir(parents=True, exist_ok=True)
    configured_state.mkdir(parents=True, exist_ok=True)

    return ServerConfig(
        data_dir=configured_data,
        reports_dir=configured_reports,
        state_dir=configured_state,
        public=public,
        enable_mutations=mutations,
        admin_token=token,
        request_timeout=timeout,
    )


def create_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    allow_public: bool | None = None,
    enable_mutations: bool | None = None,
    admin_token: str | None = None,
    data_dir: str | Path | None = None,
    reports_dir: str | Path | None = None,
    state_dir: str | Path | None = None,
    request_timeout: float | None = None,
) -> TradebotHTTPServer:
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    config = build_server_config(
        host,
        allow_public=allow_public,
        enable_mutations=enable_mutations,
        admin_token=admin_token,
        data_dir=data_dir,
        reports_dir=reports_dir,
        state_dir=state_dir,
        request_timeout=request_timeout,
    )
    return TradebotHTTPServer((host, port), config)


def run_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    allow_public: bool | None = None,
    enable_mutations: bool | None = None,
    admin_token: str | None = None,
    data_dir: str | Path | None = None,
    reports_dir: str | Path | None = None,
    state_dir: str | Path | None = None,
) -> None:
    server = create_server(
        host,
        port,
        allow_public=allow_public,
        enable_mutations=enable_mutations,
        admin_token=admin_token,
        data_dir=data_dir,
        reports_dir=reports_dir,
        state_dir=state_dir,
    )
    actual_host, actual_port = server.server_address[:2]
    print(f"Dashboard: http://{actual_host}:{actual_port}", flush=True)
    print(
        "PAPER MODE ONLY - no live trading endpoints. "
        f"mode={'public' if server.tradebot_config.public else 'local'} "
        f"mutations={'enabled' if server.tradebot_config.enable_mutations else 'disabled'}",
        flush=True,
    )
    _install_signal_handlers(server)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


def _install_signal_handlers(server: ThreadingHTTPServer) -> None:
    if threading.current_thread() is not threading.main_thread():
        return

    def stop_server(_signum, _frame) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, stop_server)
        except (ValueError, OSError):
            pass


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "TradebotDashboard/0.3"
    sys_version = ""

    @property
    def config(self) -> ServerConfig:
        configured = getattr(self.server, "tradebot_config", None)
        if configured is not None:
            return configured
        # Compatibility path for tests or embedding with a plain ThreadingHTTPServer.
        return ServerConfig(
            data_dir=Path(os.getenv("TRADEBOT_DATA_DIR", "data")).resolve(),
            reports_dir=Path(os.getenv("TRADEBOT_REPORTS_DIR", "reports")).resolve(),
            state_dir=Path(os.getenv("TRADEBOT_STATE_DIR", "paper_state")).resolve(),
            public=False,
            enable_mutations=True,
            admin_token=None,
        )

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(dashboard_html(self.config))
            return
        if path == "/health":
            self._send_json(
                {
                    "status": "ok",
                    "uptime_seconds": round(time.monotonic() - STARTED_AT, 3),
                    "mode": "public" if self.config.public else "local",
                    "mutations_enabled": self.config.enable_mutations,
                }
            )
            return
        if path == "/ready":
            ready, checks = readiness(self.config)
            self._send_json({"status": "ready" if ready else "not_ready", "checks": checks}, status=200 if ready else 503)
            return
        if path == "/reports/scanner":
            self._send_json(read_first_report(self.config.reports_dir, ["crypto_scan_ml.json", "crypto_scan.json", "crypto_scan_dashboard.json"]))
            return
        if path == "/reports/portfolio":
            self._send_json(read_first_report(self.config.reports_dir, ["crypto_portfolio_ml.json", "crypto_portfolio.json", "crypto_portfolio_dashboard.json"]))
            return
        if path == "/reports/robustness":
            self._send_json(read_first_report(self.config.reports_dir, ["crypto_robustness.json", "crypto_robustness_dashboard.json"]))
            return
        if path == "/reports/ml-comparison":
            self._send_json(read_first_report(self.config.reports_dir, ["crypto_ml_comparison.json"]))
            return
        if path == "/paper-live/state":
            self._send_json(read_json_file(self.config.state_dir / "crypto_live.json"))
            return
        if path == "/paper-live/trades":
            state = read_json_file(self.config.state_dir / "crypto_live.json")
            trades = state.get("data", {}).get("trade_history", []) if state.get("exists") else []
            self._send_json({"exists": state.get("exists", False), "trades": trades})
            return
        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            self._require_mutation_access()
            payload = self._read_body()
            if contains_forbidden_fields(payload):
                raise RequestError(400, "Forbidden live-trading or credential field rejected.")

            if path == "/run/scan":
                folder = resolve_data_folder(payload.get("folder", "crypto"), self.config.data_dir)
                top = bounded_int(payload.get("top", 20), "top", minimum=1, maximum=200)
                results = scan_crypto_folder(folder, top=top)
                output = self.config.reports_dir / "crypto_scan_dashboard.json"
                write_json(output, json.loads(to_json(results)))
                self._send_json({"message": "scan complete", "report": str(output), "results": json.loads(to_json(results))})
                return

            if path == "/run/portfolio":
                folder = resolve_data_folder(payload.get("folder", "crypto"), self.config.data_dir)
                cash = bounded_float(payload.get("cash", 100000.0), "cash", minimum=1.0, maximum=1_000_000_000.0)
                result = CryptoPortfolioPaperTrader(cash=cash).run_folder(folder)
                output = self.config.reports_dir / "crypto_portfolio_dashboard.json"
                write_json(output, json.loads(to_json(result)))
                self._send_json({"message": "portfolio complete", "report": str(output), "result": json.loads(to_json(result))})
                return

            if path == "/run/robustness":
                folder = resolve_data_folder(payload.get("folder", "crypto"), self.config.data_dir)
                cash = bounded_float(payload.get("cash", 100000.0), "cash", minimum=1.0, maximum=1_000_000_000.0)
                result = evaluate_robustness(folder, cash=cash)
                output = self.config.reports_dir / "crypto_robustness_dashboard.json"
                write_json(output, json.loads(to_json(result)))
                self._send_json({"message": "robustness complete", "report": str(output), "result": json.loads(to_json(result))})
                return

            self._send_json({"error": "Not found"}, status=404)
        except RequestError as exc:
            self._send_json({"error": str(exc)}, status=exc.status, extra_headers=exc.headers)
        except (TypeError, ValueError, OSError) as exc:
            self._send_json({"error": str(exc)}, status=400)

    def _require_mutation_access(self) -> None:
        if not self.config.enable_mutations:
            raise RequestError(403, "Mutation endpoints are disabled for this deployment")
        token = self.config.admin_token
        if token is None:
            return
        authorization = self.headers.get("Authorization", "")
        supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else self.headers.get("X-Admin-Token", "").strip()
        if not supplied or not secrets.compare_digest(supplied, token):
            raise RequestError(401, "Valid admin token required", {"WWW-Authenticate": "Bearer"})

    def log_message(self, format: str, *args: Any) -> None:
        event = {
            "event": "http_request",
            "client": self.client_address[0] if self.client_address else None,
            "request": self.requestline,
            "message": format % args,
        }
        print(json.dumps(event, ensure_ascii=True), flush=True)

    def _read_body(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"", "application/json"}:
            raise RequestError(415, "Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError as exc:
            raise RequestError(400, "Invalid Content-Length") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise RequestError(413, f"Request body must not exceed {MAX_REQUEST_BYTES} bytes")
        if length == 0:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestError(400, "Request body must be valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise RequestError(400, "JSON request body must be an object")
        return payload

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'")

    def _send_json(
        self,
        payload: dict[str, Any],
        status: int = 200,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        response = with_warning(payload)
        body = json.dumps(response, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._security_headers()
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
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


def resolve_data_folder(value: Any, allowed_root: str | Path | None = None) -> Path:
    root = Path(allowed_root or os.getenv("TRADEBOT_DATA_DIR", "data")).expanduser().resolve()
    requested = Path(str(value))
    candidate = (root / requested).resolve() if not requested.is_absolute() else requested.expanduser().resolve()
    # Preserve compatibility with callers that pass data/crypto while the root is ./data.
    if not requested.is_absolute() and requested.parts and requested.parts[0] == root.name:
        candidate = (root.parent / requested).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Dashboard folder must be inside the configured data directory")
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


def readiness(config: ServerConfig) -> tuple[bool, dict[str, bool]]:
    checks = {
        "data_directory_readable": config.data_dir.is_dir() and os.access(config.data_dir, os.R_OK),
        "reports_directory_writable": config.reports_dir.is_dir() and os.access(config.reports_dir, os.W_OK),
        "state_directory_writable": config.state_dir.is_dir() and os.access(config.state_dir, os.W_OK),
    }
    return all(checks.values()), checks


def read_first_report(directory: Path, names: list[str]) -> dict[str, Any]:
    for name in names:
        result = read_json_file(directory / name)
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
    serialized = json.dumps(payload, indent=2, default=str)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def dashboard_html(config: ServerConfig | None = None) -> str:
    mode = "PUBLIC READ-ONLY" if config and config.public and not config.enable_mutations else "PAPER MODE ONLY"
    return f"""<!doctype html>
<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Dual Market AI Bot Dashboard</title>
<style>body{{font-family:system-ui,sans-serif;margin:24px;background:#0f172a;color:#e2e8f0}}.warn{{background:#7f1d1d;padding:16px;border-radius:8px;font-size:20px;font-weight:bold}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}}.card{{background:#1e293b;padding:16px;border-radius:8px}}pre{{white-space:pre-wrap;max-height:360px;overflow:auto;font-size:12px}}small{{color:#94a3b8}}</style></head>
<body><div class='warn'>{mode} — no live trading, wallets, order endpoints, leverage, futures, API keys, or guaranteed profit.</div>
<h1>Dual Market AI Bot</h1><small>Health: <span id='health'>checking...</span></small><div class='grid'>
<div class='card'><h2>Scanner</h2><pre id='scanner'>Loading...</pre></div>
<div class='card'><h2>Portfolio</h2><pre id='portfolio'>Loading...</pre></div>
<div class='card'><h2>Robustness</h2><pre id='robustness'>Loading...</pre></div>
<div class='card'><h2>ML Comparison</h2><pre id='ml'>Loading...</pre></div>
<div class='card'><h2>Paper-live State</h2><pre id='state'>Loading...</pre></div>
<div class='card'><h2>Paper-live Trades</h2><pre id='trades'>Loading...</pre></div>
</div><script>
async function load(id,url){{try{{const r=await fetch(url,{{cache:'no-store'}});const j=await r.json();document.getElementById(id).textContent=JSON.stringify(j,null,2);return j}}catch(e){{document.getElementById(id).textContent=String(e)}}}}
load('health','/health').then(j=>{{if(j)document.getElementById('health').textContent=j.status+' / '+j.mode}});load('scanner','/reports/scanner');load('portfolio','/reports/portfolio');load('robustness','/reports/robustness');load('ml','/reports/ml-comparison');load('state','/paper-live/state');load('trades','/paper-live/trades');
</script></body></html>"""
