from __future__ import annotations

import json
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tradebot.api.server import DashboardHandler


def start_server(tmp_path):
    old = os.getcwd()
    os.chdir(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    return server, base, old


def get_json(base, path):
    with urlopen(base + path, timeout=5) as response:
        return json.loads(response.read().decode())


def post_json(base, path, payload):
    data = json.dumps(payload).encode()
    request = Request(base + path, data=data, method="POST", headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode())


def stop_server(server, old_cwd):
    server.shutdown()
    server.server_close()
    os.chdir(old_cwd)


def test_health_endpoint(tmp_path):
    server, base, old = start_server(tmp_path)
    try:
        payload = get_json(base, "/health")
        assert payload["status"] == "ok"
        assert payload["paper_only"] is True
    finally:
        stop_server(server, old)


def test_report_endpoint_when_file_exists(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "crypto_scan.json").write_text(json.dumps([{"symbol": "BTCUSDT"}]))
    server, base, old = start_server(tmp_path)
    try:
        payload = get_json(base, "/reports/scanner")
        assert payload["exists"] is True
        assert payload["data"][0]["symbol"] == "BTCUSDT"
    finally:
        stop_server(server, old)


def test_report_endpoint_when_file_missing(tmp_path):
    server, base, old = start_server(tmp_path)
    try:
        payload = get_json(base, "/reports/portfolio")
        assert payload["exists"] is False
        assert payload["paper_only"] is True
    finally:
        stop_server(server, old)


def test_rejecting_forbidden_live_trading_fields(tmp_path):
    server, base, old = start_server(tmp_path)
    try:
        request = Request(base + "/run/scan", data=json.dumps({"api_key": "bad"}).encode(), method="POST", headers={"Content-Type": "application/json"})
        try:
            urlopen(request, timeout=5)
            assert False, "request should fail"
        except HTTPError as exc:
            payload = json.loads(exc.read().decode())
            assert exc.code == 400
            assert payload["paper_only"] is True
            assert "Forbidden" in payload["error"]
    finally:
        stop_server(server, old)


def test_no_order_endpoints_exist(tmp_path):
    server, base, old = start_server(tmp_path)
    try:
        try:
            get_json(base, "/orders")
            assert False, "orders endpoint should not exist"
        except HTTPError as exc:
            assert exc.code == 404
    finally:
        stop_server(server, old)


def test_paper_live_trades_endpoint(tmp_path):
    (tmp_path / "paper_state").mkdir()
    (tmp_path / "paper_state" / "crypto_live.json").write_text(json.dumps({"trade_history": [{"symbol": "BTCUSDT"}]}))
    server, base, old = start_server(tmp_path)
    try:
        payload = get_json(base, "/paper-live/trades")
        assert payload["paper_only"] is True
        assert payload["trades"][0]["symbol"] == "BTCUSDT"
    finally:
        stop_server(server, old)
