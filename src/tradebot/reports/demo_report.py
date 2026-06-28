from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

REPORTS_DIR = Path("reports")
PAPER_STATE_DIR = Path("paper_state")


@dataclass
class DemoReportSummary:
    generated_at: str
    paper_only: bool
    disclaimer: str
    included_reports: dict[str, bool]
    key_warnings: list[str]
    next_milestones: list[str]


def generate_demo_report(out: str | Path, json_out: str | Path | None = None) -> DemoReportSummary:
    output = Path(out)
    output.parent.mkdir(parents=True, exist_ok=True)
    latest = collect_latest_results()
    summary = DemoReportSummary(
        generated_at=datetime.now(UTC).replace(tzinfo=None).isoformat(),
        paper_only=True,
        disclaimer="Not financial advice. No guaranteed returns. Real trading can lose money.",
        included_reports={name: payload is not None for name, payload in latest.items()},
        key_warnings=[
            "All results are paper/simulation results and are not proof of future profit.",
            "No live trading, wallets, exchange order APIs, leverage, futures, or API keys are included.",
            "Tax, fee, and slippage calculations are estimates and need professional review.",
        ],
        next_milestones=[
            "Larger real data testing",
            "Longer paper-live testing",
            "Improved ML with stronger validation",
            "Dashboard/API hardening and APK/mobile dashboard",
            "Legal/compliance review",
            "Only then consider a tiny real-money pilot with kill switch",
        ],
    )
    output.write_text(render_markdown(summary, latest), encoding="utf-8")
    if json_out:
        json_path = Path(json_out)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps({"summary": asdict(summary), "latest_results": latest}, indent=2, default=str), encoding="utf-8")
    return summary


def collect_latest_results() -> dict[str, Any | None]:
    return {
        "scanner": read_first([REPORTS_DIR / "crypto_scan_ml.json", REPORTS_DIR / "crypto_scan.json", REPORTS_DIR / "crypto_scan_dashboard.json"]),
        "portfolio": read_first([REPORTS_DIR / "crypto_portfolio_ml.json", REPORTS_DIR / "crypto_portfolio.json", REPORTS_DIR / "crypto_portfolio_dashboard.json"]),
        "robustness": read_first([REPORTS_DIR / "crypto_robustness.json", REPORTS_DIR / "crypto_robustness_dashboard.json"]),
        "ml_comparison": read_first([REPORTS_DIR / "crypto_ml_comparison.json"]),
        "paper_live_state": read_first([PAPER_STATE_DIR / "crypto_live.json"]),
    }


def read_first(paths: list[Path]) -> Any | None:
    for path in paths:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {"error": f"Could not parse {path}"}
    return None


def render_markdown(summary: DemoReportSummary, latest: dict[str, Any | None]) -> str:
    sections = [
        "# Dual Market AI Bot — Investor Demo Report",
        f"Generated: {summary.generated_at}",
        "",
        "## 1. Project summary",
        "Dual Market AI Bot is a paper-only research platform for crypto and Indian equity strategy testing. It combines CSV data validation, scanner ranking, cost/tax estimates, risk controls, backtesting, walk-forward testing, robustness testing, ML scoring, paper-live simulation, and a local dashboard/API.",
        "",
        "## 2. Problem statement",
        "Retail traders often see unrealistic profit bots that hide costs, taxes, drawdowns, overfitting, and execution risk. This project focuses on transparent paper testing before any live-money decision.",
        "",
        "## 3. What the bot does today",
        "- Loads and validates OHLCV candles.\n- Fetches public/read-only crypto historical candles where network access allows.\n- Scans crypto/equity CSVs and ranks opportunities.\n- Simulates paper backtests, portfolio rotation, robustness windows, ML scoring, and paper-live loops.\n- Estimates fees, slippage, brokerage, and simplified Indian tax impact.\n- Produces console, JSON, Markdown, and local dashboard views.",
        "",
        "## 4. What it does not do yet",
        "- It does not place real trades.\n- It does not connect wallets.\n- It does not call exchange or broker order APIs.\n- It does not store API keys.\n- It does not use leverage, futures, or options.\n- It does not guarantee profit.",
        "",
        "## 5. Architecture overview",
        "The backend is modular: data providers and CSV loaders feed scanners, strategies, risk/cost/tax engines, paper backtest engines, ML scoring, robustness evaluation, paper-live state, reports, CLI commands, and a local read-only dashboard/API.",
        "",
        "## 6. Paper-only safety rules",
        "- Paper-only first.\n- No live trading in v1.\n- No wallet permissions.\n- No withdrawal permissions.\n- No order endpoints.\n- No leverage/futures.\n- Explicit risk warnings in reports and dashboard.",
        "",
        "## 7. Current modules",
        "- Crypto data fetcher: public/read-only market data only.\n- Scanner: opportunity/risk ranking with rejection reasons.\n- Tax/cost engine: estimated fees, slippage, brokerage, and tax impact.\n- ML scoring: supervised paper-research score, not an auto-trade decision.\n- Portfolio rotation: one-position paper rotation across symbols.\n- Robustness testing: evaluates many time windows and market regimes.\n- Paper-live mode: live-like fake-money loop with persisted state.\n- Dashboard/API: local report and state viewer with forbidden-field safety checks.",
        "",
        "## 8. Latest available results",
        result_block("Scanner report", latest["scanner"]),
        result_block("Portfolio report", latest["portfolio"]),
        result_block("Robustness report", latest["robustness"]),
        result_block("ML comparison", latest["ml_comparison"]),
        result_block("Paper-live state", latest["paper_live_state"]),
        "",
        "## 9. Risk section",
        "- Market risk: prices can gap, trend regimes can change, and losses can cluster.\n- Overfitting risk: strategies or ML may fit historical noise.\n- Tax/fee risk: costs can erase gross gains.\n- API/security risk: future integrations require strict read-only defaults and secrets handling.\n- Regulatory risk: market, tax, and advisory rules vary by jurisdiction.",
        "",
        "## 10. Risk minimization",
        "- Paper-only first.\n- No leverage.\n- No withdrawals.\n- No live trading in v1.\n- Stop-loss and max daily loss controls.\n- Tax-aware net profit calculations.\n- Robustness and walk-forward testing before any production consideration.",
        "",
        "## 11. Roadmap",
        "- Larger real data testing.\n- Longer paper-live testing.\n- Improved ML and model validation.\n- APK/mobile dashboard after backend stability.\n- Legal/compliance review.\n- Only then, a tiny real-money pilot with kill switch may be considered.",
        "",
        "## 12. Investor explanation",
        "This can become a research and monitoring product: users could subscribe to paper-tested analytics, scanner dashboards, robustness reports, and education-grade risk tools. It is different from fake profit bots because it explicitly includes costs, taxes, drawdowns, rejection reasons, no-profit guarantees, and paper-only validation gates.",
        "",
        "## 13. Clear disclaimer",
        "This is not financial advice. There are no guaranteed returns. Paper results are not proof of future profit. Real trading can lose money, including the full amount at risk.",
    ]
    return "\n".join(sections) + "\n"


def result_block(title: str, payload: Any | None) -> str:
    if payload is None:
        return f"### {title}\nNo report available yet."
    preview = json.dumps(payload, indent=2, default=str)[:2500]
    return f"### {title}\n```json\n{preview}\n```"
