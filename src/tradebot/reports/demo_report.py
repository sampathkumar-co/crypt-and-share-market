from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
        generated_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        paper_only=True,
        disclaimer="Not financial advice. No guaranteed returns. Real trading can lose money.",
        included_reports={name: payload is not None for name, payload in latest.items()},
        key_warnings=[
            "All results are paper or simulation results and are not proof of future profit.",
            "The project does not place real trades or contain wallet, broker-order, leverage, or private-key functionality.",
            "Tax, fee, slippage, and TDS calculations are configurable estimates requiring professional review.",
        ],
        next_milestones=[
            "Higher-quality adjusted datasets and provenance records",
            "Execution and slippage sensitivity analysis",
            "Purged validation, confidence intervals, and overfitting diagnostics",
            "Model drift, calibration, and reproducible model cards",
            "Improved local charts and experiment comparison",
        ],
    )
    output.write_text(render_markdown(summary, latest), encoding="utf-8")
    if json_out:
        json_path = Path(json_out)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps({"summary": asdict(summary), "latest_results": latest}, indent=2, default=str),
            encoding="utf-8",
        )
    return summary


def collect_latest_results() -> dict[str, Any | None]:
    return {
        "scanner": read_first(
            [
                REPORTS_DIR / "crypto_scan_ml.json",
                REPORTS_DIR / "crypto_scan.json",
                REPORTS_DIR / "crypto_scan_dashboard.json",
            ]
        ),
        "portfolio": read_first(
            [
                REPORTS_DIR / "crypto_portfolio_ml.json",
                REPORTS_DIR / "crypto_portfolio.json",
                REPORTS_DIR / "crypto_portfolio_dashboard.json",
            ]
        ),
        "robustness": read_first(
            [REPORTS_DIR / "crypto_robustness.json", REPORTS_DIR / "crypto_robustness_dashboard.json"]
        ),
        "ml_comparison": read_first([REPORTS_DIR / "crypto_ml_comparison.json"]),
        "paper_live_state": read_first([PAPER_STATE_DIR / "crypto_live.json"]),
    }


def read_first(paths: list[Path]) -> Any | None:
    for path in paths:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {"error": f"Could not parse {path}"}
    return None


def render_markdown(summary: DemoReportSummary, latest: dict[str, Any | None]) -> str:
    sections = [
        "# Dual Market AI Bot — Paper Research Demo Report",
        f"Generated: {summary.generated_at}",
        "",
        "## 1. Project summary",
        "Dual Market AI Bot is a paper-only research platform for crypto and Indian equity strategy testing. It combines validated market data, realistic next-bar backtesting, scanners, cost and tax estimates, portfolio rotation, walk-forward testing, robustness analysis, optional ML scoring, paper-live simulation, and a local dashboard.",
        "",
        "## 2. Problem statement",
        "Many trading demonstrations hide costs, taxes, drawdowns, overfitting, and impossible same-candle execution. This project makes those assumptions visible and keeps real-money execution outside the repository.",
        "",
        "## 3. What the platform does today",
        "- Loads, validates, and audits OHLCV candles.\n- Fetches public/read-only crypto history where network access allows.\n- Tests momentum, breakout, and mean-reversion strategies.\n- Executes historical signals at the next available candle open.\n- Scans crypto and equity data and ranks opportunities.\n- Simulates portfolio rotation, robustness windows, ML scoring, and paper-live loops.\n- Reports fees, slippage, simplified tax estimates, VDA TDS cash flow, drawdown, benchmark-relative results, and risk-adjusted metrics.",
        "",
        "## 4. Permanent safety boundary",
        "- It is paper-only.\n- It does not place real trades.\n- It does not connect wallets or broker/exchange order APIs.\n- It does not store API keys, seeds, or private keys.\n- It does not use leverage, futures, or options.\n- It does not guarantee returns.\n- Any future live executor would require a separate repository and independent review.",
        "",
        "## 5. Research-integrity controls",
        "- Completed candles only for signal generation.\n- Next-open execution rather than impossible same-close fills.\n- Conservative default when both stop and target occur inside one OHLC candle.\n- Training and unseen walk-forward evaluation remain separated.\n- Duplicate timestamps and malformed market data are rejected.\n- Results are compared with buy-and-hold and include risk-adjusted metrics.",
        "",
        "## 6. Current modules",
        "- Public crypto data and CSV auditing.\n- Crypto and equity scanners.\n- Risk, cost, slippage, tax, and TDS estimates.\n- Single-symbol and portfolio backtests.\n- Walk-forward and robustness analysis.\n- Optional ML scoring and baseline comparison.\n- Resumable paper-live state.\n- Loopback-only local dashboard.\n- CLI, JSON, Markdown, tests, and CI.",
        "",
        "## 7. Latest available results",
        result_block("Scanner report", latest["scanner"]),
        result_block("Portfolio report", latest["portfolio"]),
        result_block("Robustness report", latest["robustness"]),
        result_block("ML comparison", latest["ml_comparison"]),
        result_block("Paper-live state", latest["paper_live_state"]),
        "",
        "## 8. Key risks",
        "- Historical selection and survivorship bias.\n- Regime change and model drift.\n- OHLC ambiguity, latency, partial fills, market impact, and outages.\n- Public-data gaps or revisions.\n- Costs and taxes differing from simplified assumptions.\n- Small samples and multiple-testing bias.",
        "",
        "## 9. Next research milestones",
        "\n".join(f"- {milestone}" for milestone in summary.next_milestones),
        "",
        "## 10. Product potential",
        "The credible product direction is research and monitoring: transparent scanners, paper portfolios, robustness reports, experiment tracking, and education-grade risk tools. Its value is honest evidence and reproducibility—not a claim of guaranteed profit.",
        "",
        "## 11. Clear disclaimer",
        "This is not financial advice. There are no guaranteed returns. Paper results are not proof of future profit. Real trading can lose money, including the full amount at risk.",
    ]
    return "\n".join(sections) + "\n"


def result_block(title: str, payload: Any | None) -> str:
    if payload is None:
        return f"### {title}\nNo report available yet."
    preview = json.dumps(payload, indent=2, default=str)[:2500]
    return f"### {title}\n```json\n{preview}\n```"
