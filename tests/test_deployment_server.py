from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tradebot.api.server import create_server


def deployment_dirs(tmp_path):
    data = tmp_path / "data"
    reports = tmp_path / "runtime" / "reports"
    state = tmp_path / "runtime" / "paper_state"
    (data / "crypto").mkdir(parents=True)
    reports.mkdir(parents=True)
    state.mkdir(parents=True)
    return data, reports, state


def start(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_address[1]}"


def stop(server):
    server.shutdown()
    server.server_close()


def get_json(base, path):
    with urlopen(base + path, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def post(base, path, *, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(base + path, data=b"{}", method="POST", headers=headers)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_public_server_is_read_only_by_default(tmp_path):
    data, reports, state = deployment_dirs(tmp_path)
    server = create_server(
        "0.0.0.0",
        0,
        allow_public=True,
        data_dir=data,
        reports_dir=reports,
        state_dir=state,
    )
    base = start(server)
    try:
        status, health = get_json(base, "/health")
        assert status == 200
        assert health["mode"] == "public"
        assert health["mutations_enabled"] is False

        status, ready = get_json(base, "/ready")
        assert status == 200
        assert ready["status"] == "ready"

        status, body = post(base, "/run/scan")
        assert status == 403
        assert "disabled" in body["error"]
    finally:
        stop(server)


def test_public_mutations_require_strong_token(tmp_path):
    data, reports, state = deployment_dirs(tmp_path)
    with pytest.raises(ValueError, match="at least 32 characters"):
        create_server(
            "0.0.0.0",
            0,
            allow_public=True,
            enable_mutations=True,
            admin_token="too-short",
            data_dir=data,
            reports_dir=reports,
            state_dir=state,
        )


def test_public_mutations_require_valid_bearer_token(tmp_path):
    data, reports, state = deployment_dirs(tmp_path)
    token = "a" * 48
    server = create_server(
        "0.0.0.0",
        0,
        allow_public=True,
        enable_mutations=True,
        admin_token=token,
        data_dir=data,
        reports_dir=reports,
        state_dir=state,
    )
    base = start(server)
    try:
        status, body = post(base, "/run/not-a-route")
        assert status == 401
        assert "token" in body["error"].lower()

        status, body = post(base, "/run/not-a-route", token="wrong" * 10)
        assert status == 401

        status, body = post(base, "/run/not-a-route", token=token)
        assert status == 404
        assert body["error"] == "Not found"
    finally:
        stop(server)
