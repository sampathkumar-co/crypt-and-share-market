from __future__ import annotations

import json
import os
from pathlib import Path

from tradebot.reports.demo_report import generate_demo_report


def test_report_generation_when_reports_exist(tmp_path):
    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        Path("reports").mkdir()
        Path("reports/crypto_scan.json").write_text(json.dumps([{"symbol": "BTCUSDT"}]))
        summary = generate_demo_report("reports/investor_demo_report.md")
        text = Path("reports/investor_demo_report.md").read_text()
        assert summary.included_reports["scanner"] is True
        assert "BTCUSDT" in text
        assert "Latest available results" in text
    finally:
        os.chdir(old)


def test_report_generation_when_reports_missing(tmp_path):
    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        summary = generate_demo_report("reports/investor_demo_report.md")
        text = Path("reports/investor_demo_report.md").read_text()
        assert summary.included_reports["scanner"] is False
        assert "No report available yet" in text
    finally:
        os.chdir(old)


def test_report_includes_no_guarantee_disclaimer(tmp_path):
    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        generate_demo_report("reports/investor_demo_report.md")
        text = Path("reports/investor_demo_report.md").read_text().lower()
        assert "no guaranteed returns" in text
        assert "not financial advice" in text
    finally:
        os.chdir(old)


def test_report_includes_paper_only_statement(tmp_path):
    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        generate_demo_report("reports/investor_demo_report.md")
        text = Path("reports/investor_demo_report.md").read_text().lower()
        assert "paper-only" in text
        assert "does not place real trades" in text
    finally:
        os.chdir(old)


def test_json_summary_shape(tmp_path):
    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        generate_demo_report("reports/investor_demo_report.md", json_out="reports/investor_demo_summary.json")
        payload = json.loads(Path("reports/investor_demo_summary.json").read_text())
        assert payload["summary"]["paper_only"] is True
        assert "included_reports" in payload["summary"]
        assert "latest_results" in payload
    finally:
        os.chdir(old)
