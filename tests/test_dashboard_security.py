from __future__ import annotations

import pytest

from tradebot.api.server import resolve_data_folder, run_server


def test_dashboard_data_folder_cannot_escape_project_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "crypto").mkdir(parents=True)

    assert resolve_data_folder("data/crypto") == (tmp_path / "data" / "crypto").resolve()
    with pytest.raises(ValueError, match="inside the local data directory"):
        resolve_data_folder("../outside")
    with pytest.raises(ValueError, match="inside the local data directory"):
        resolve_data_folder(tmp_path.parent)


def test_dashboard_refuses_non_loopback_binding():
    with pytest.raises(ValueError, match="local-only"):
        run_server("0.0.0.0", 8000)
