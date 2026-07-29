from __future__ import annotations

import argparse
import os
from pathlib import Path

from tradebot.api.server import run_server
from tradebot.backtest.ml_comparison import compare_crypto_ml
from tradebot.backtest.paper_live import PaperLiveCryptoBot
from tradebot.backtest.paper_trader import BacktestConfig, PaperTrader
from tradebot.backtest.portfolio_trader import CryptoPortfolioPaperTrader, PortfolioConfig
from tradebot.backtest.research_gate import (
    ResearchGateConfig,
    evaluate_research_gate,
    write_gate_report,
)
from tradebot.backtest.robustness import evaluate_robustness
from tradebot.backtest.walk_forward import build_strategy, walk_forward
from tradebot.data.csv_loader import audit_candles, load_candles
from tradebot.data.crypto_provider import PublicCryptoHistoricalClient
from tradebot.ml.crypto_signal_model import CryptoSignalModel, evaluate_folder, train_from_folder
from tradebot.models import Market
from tradebot.reports.demo_report import generate_demo_report
from tradebot.reports.report_generator import (
    backtest_console,
    ml_comparison_console,
    portfolio_console,
    research_gate_console,
    robustness_console,
    scan_console,
    to_json,
    walk_forward_console,
)
from tradebot.scanner.crypto_scanner import scan_crypto_folder
from tradebot.scanner.equity_scanner import scan_equity_folder

STRATEGIES = ["momentum", "breakout", "mean_reversion"]


def parse_market(value: str) -> Market:
    return Market(value.lower())


def write_json(path: str, content: str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(output)


def _environment_port() -> int:
    try:
        return int(os.getenv("PORT", "8000"))
    except ValueError as exc:
        raise ValueError("PORT must be an integer") from exc


def _add_execution_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--min-holding-bars", type=int, default=2)
    parser.add_argument("--max-holding-bars", type=int, default=40)
    parser.add_argument("--cooldown-bars", type=int, default=1)
    parser.add_argument("--exit-confirmation-bars", type=int, default=2)
    parser.add_argument("--trailing-stop-pct", type=float, default=0.03)
    parser.add_argument("--breakeven-trigger-pct", type=float, default=0.02)
    parser.add_argument(
        "--regime-filter",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Block entries outside the strategy's suitable market regime.",
    )


def _backtest_config(args) -> BacktestConfig:
    return BacktestConfig(
        intrabar_policy=getattr(args, "intrabar_policy", "worst_case"),
        min_holding_bars=args.min_holding_bars,
        max_holding_bars=args.max_holding_bars,
        cooldown_bars=args.cooldown_bars,
        exit_confirmation_bars=args.exit_confirmation_bars,
        trailing_stop_pct=args.trailing_stop_pct,
        breakeven_trigger_pct=args.breakeven_trigger_pct,
        use_regime_filter=args.regime_filter,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="Dual Market AI Bot",
        description="Safe paper-trading research CLI. No real orders.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    backtest_parser = sub.add_parser("backtest")
    backtest_parser.add_argument("--market", required=True, choices=[market.value for market in Market])
    backtest_parser.add_argument("--symbol", required=True)
    backtest_parser.add_argument("--data", required=True)
    backtest_parser.add_argument("--strategy", choices=STRATEGIES, default="momentum")
    backtest_parser.add_argument("--cash", type=float, default=100000.0)
    backtest_parser.add_argument("--intrabar-policy", choices=["worst_case", "best_case"], default="worst_case")
    backtest_parser.add_argument("--json-out")
    _add_execution_arguments(backtest_parser)

    scan_parser = sub.add_parser("scan")
    scan_parser.add_argument("--market", required=True, choices=[market.value for market in Market])
    scan_parser.add_argument("--folder", required=True)
    scan_parser.add_argument("--json-out")
    scan_parser.add_argument("--top", type=int, default=None)
    scan_parser.add_argument("--model")

    walk_parser = sub.add_parser("walk-forward")
    walk_parser.add_argument("--market", required=True, choices=[market.value for market in Market])
    walk_parser.add_argument("--symbol", required=True)
    walk_parser.add_argument("--data", required=True)
    walk_parser.add_argument("--strategy", choices=STRATEGIES, default="momentum")
    walk_parser.add_argument("--train-size", type=int)
    walk_parser.add_argument("--test-size", type=int)
    walk_parser.add_argument("--json-out")
    _add_execution_arguments(walk_parser)

    gate_parser = sub.add_parser(
        "research-gate",
        help="Optimize on training periods and enforce positive, cost-aware unseen results before continuous paper mode.",
    )
    gate_parser.add_argument("--market", choices=[market.value for market in Market], default="crypto")
    gate_parser.add_argument("--folder", required=True)
    gate_parser.add_argument("--train-size", type=int, default=180)
    gate_parser.add_argument("--test-size", type=int, default=60)
    gate_parser.add_argument("--min-periods", type=int, default=3)
    gate_parser.add_argument("--max-drawdown", type=float, default=0.20)
    gate_parser.add_argument("--max-cost-drag", type=float, default=0.50)
    gate_parser.add_argument("--max-trades-per-100", type=float, default=8.0)
    gate_parser.add_argument("--min-average-holding-bars", type=float, default=2.0)
    gate_parser.add_argument("--min-beat-buy-hold-fraction", type=float, default=0.50)
    gate_parser.add_argument("--json-out", required=True)

    validate_parser = sub.add_parser("validate-data")
    validate_parser.add_argument("--data", required=True)
    validate_parser.add_argument("--json-out")

    fetch_parser = sub.add_parser("fetch-crypto")
    fetch_parser.add_argument("--symbols", required=True, help="Comma-separated symbols, for example BTCUSDT,ETHUSDT")
    fetch_parser.add_argument("--interval", default="1d")
    fetch_parser.add_argument("--days", type=int, default=365)
    fetch_parser.add_argument("--out", default="data/crypto")

    portfolio_parser = sub.add_parser("portfolio-crypto")
    portfolio_parser.add_argument("--folder", required=True)
    portfolio_parser.add_argument("--cash", type=float, default=100000.0)
    portfolio_parser.add_argument("--top", type=int, default=20)
    portfolio_parser.add_argument("--min-holding-bars", type=int, default=3)
    portfolio_parser.add_argument("--max-holding-bars", type=int, default=30)
    portfolio_parser.add_argument("--cooldown-bars", type=int, default=2)
    portfolio_parser.add_argument("--max-trades-per-100", type=float, default=8.0)
    portfolio_parser.add_argument("--json-out")
    portfolio_parser.add_argument("--model")

    robustness_parser = sub.add_parser("robustness-crypto")
    robustness_parser.add_argument("--folder", required=True)
    robustness_parser.add_argument("--cash", type=float, default=100000.0)
    robustness_parser.add_argument("--json-out")
    robustness_parser.add_argument("--model")

    train_ml_parser = sub.add_parser("train-crypto-ml")
    train_ml_parser.add_argument("--folder", required=True)
    train_ml_parser.add_argument("--model-out", required=True)

    eval_ml_parser = sub.add_parser("evaluate-crypto-ml")
    eval_ml_parser.add_argument("--folder", required=True)
    eval_ml_parser.add_argument("--model", required=True)
    eval_ml_parser.add_argument("--json-out")

    compare_ml_parser = sub.add_parser("compare-crypto-ml")
    compare_ml_parser.add_argument("--folder", required=True)
    compare_ml_parser.add_argument("--cash", type=float, default=100000.0)
    compare_ml_parser.add_argument("--model", required=True)
    compare_ml_parser.add_argument("--json-out")

    paper_live_parser = sub.add_parser("paper-live-crypto")
    paper_live_parser.add_argument("--symbols", required=True)
    paper_live_parser.add_argument("--interval", default="1m")
    paper_live_parser.add_argument("--cash", type=float, default=100000.0)
    paper_live_parser.add_argument("--model")
    paper_live_parser.add_argument("--state", required=True)
    paper_live_parser.add_argument("--strategy", choices=STRATEGIES, default="momentum")
    paper_live_parser.add_argument("--max-loops", type=int, default=1)
    paper_live_parser.add_argument("--sleep-seconds", type=float, default=60.0)
    paper_live_parser.add_argument(
        "--continuous",
        action="store_true",
        help="Run continuously only after validating a fresh passing research-gate report.",
    )
    paper_live_parser.add_argument("--gate-report")
    paper_live_parser.add_argument("--gate-max-age-days", type=int, default=90)

    dashboard_parser = sub.add_parser("serve-dashboard")
    dashboard_parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    dashboard_parser.add_argument("--port", type=int, default=_environment_port())
    dashboard_parser.add_argument(
        "--public",
        action="store_true",
        help="Explicitly permit binding a non-loopback address. Public mode is read-only by default.",
    )
    mutation_group = dashboard_parser.add_mutually_exclusive_group()
    mutation_group.add_argument(
        "--enable-mutations",
        action="store_true",
        dest="enable_mutations",
        default=None,
        help="Enable scan/portfolio/robustness POST actions. Public mode also requires TRADEBOT_ADMIN_TOKEN.",
    )
    mutation_group.add_argument(
        "--read-only",
        action="store_false",
        dest="enable_mutations",
        help="Disable all POST research actions.",
    )
    dashboard_parser.add_argument("--data-dir", default=None)
    dashboard_parser.add_argument("--reports-dir", default=None)
    dashboard_parser.add_argument("--state-dir", default=None)

    demo_parser = sub.add_parser("demo-report")
    demo_parser.add_argument("--out", required=True)
    demo_parser.add_argument("--json-out")

    args = parser.parse_args(argv)

    if args.cmd == "demo-report":
        summary = generate_demo_report(args.out, json_out=args.json_out)
        print(f"Demo report written to {args.out}")
        if args.json_out:
            print(f"Demo JSON summary written to {args.json_out}")
        print(summary.disclaimer)
        return 0

    if args.cmd == "serve-dashboard":
        run_server(
            args.host,
            args.port,
            allow_public=args.public or None,
            enable_mutations=args.enable_mutations,
            data_dir=args.data_dir,
            reports_dir=args.reports_dir,
            state_dir=args.state_dir,
        )
        return 0

    if args.cmd == "research-gate":
        gate_config = ResearchGateConfig(
            train_size=args.train_size,
            test_size=args.test_size,
            min_independent_periods=args.min_periods,
            max_unseen_drawdown=args.max_drawdown,
            max_cost_drag_ratio=args.max_cost_drag,
            max_trades_per_100_bars=args.max_trades_per_100,
            min_average_holding_bars=args.min_average_holding_bars,
            min_beat_buy_hold_fraction=args.min_beat_buy_hold_fraction,
        )
        result = evaluate_research_gate(
            args.folder,
            market=parse_market(args.market),
            config=gate_config,
        )
        write_gate_report(args.json_out, result)
        print(research_gate_console(result))
        print(f"Gate report written to {args.json_out}")
        return 0 if result.accepted else 2

    if args.cmd == "paper-live-crypto":
        model = CryptoSignalModel.load(args.model) if args.model else None
        symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
        bot = PaperLiveCryptoBot(
            symbols,
            args.interval,
            args.cash,
            args.state,
            model=model,
            strategy_name=args.strategy,
        )
        if args.continuous:
            if not args.gate_report:
                parser.error("--continuous requires --gate-report")
            bot.run_continuous(
                sleep_seconds=args.sleep_seconds,
                gate_report_path=args.gate_report,
                gate_max_age_days=args.gate_max_age_days,
            )
        else:
            bot.run(max_loops=args.max_loops, sleep_seconds=args.sleep_seconds)
        return 0

    if args.cmd == "compare-crypto-ml":
        result = compare_crypto_ml(args.folder, args.model, cash=args.cash)
        print(ml_comparison_console(result))
        if args.json_out:
            write_json(args.json_out, to_json(result))
        return 0

    if args.cmd == "train-crypto-ml":
        model = train_from_folder(args.folder, args.model_out)
        print(f"Saved crypto ML model with {model.samples} training samples -> {args.model_out}")
        print("WARNING: ML score is paper-research only and does not prove profit.")
        return 0

    if args.cmd == "evaluate-crypto-ml":
        metrics = evaluate_folder(args.folder, args.model)
        print(to_json(metrics))
        if args.json_out:
            write_json(args.json_out, to_json(metrics))
        return 0

    if args.cmd == "robustness-crypto":
        model = CryptoSignalModel.load(args.model) if getattr(args, "model", None) else None
        result = evaluate_robustness(args.folder, cash=args.cash, model=model)
        print(robustness_console(result))
        if args.json_out:
            write_json(args.json_out, to_json(result))
        return 0

    if args.cmd == "portfolio-crypto":
        model = CryptoSignalModel.load(args.model) if getattr(args, "model", None) else None
        result = CryptoPortfolioPaperTrader(
            cash=args.cash,
            config=PortfolioConfig(
                scanner_top=args.top,
                min_holding_bars=args.min_holding_bars,
                max_holding_bars=args.max_holding_bars,
                cooldown_bars=args.cooldown_bars,
                max_trades_per_100_bars=args.max_trades_per_100,
            ),
            model=model,
        ).run_folder(args.folder)
        print(portfolio_console(result))
        if args.json_out:
            write_json(args.json_out, to_json(result))
        return 0

    if args.cmd == "fetch-crypto":
        symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
        client = PublicCryptoHistoricalClient()
        results = client.fetch_symbols_to_csv(symbols, interval=args.interval, days=args.days, out_dir=args.out)
        for result in results:
            if result.error:
                print(f"FAILED {result.symbol}: {result.error}")
            else:
                print(f"SAVED {result.symbol}: {result.candles} candles -> {result.path}")
        return 1 if any(result.error for result in results) else 0

    if args.cmd == "validate-data":
        report = audit_candles(load_candles(args.data))
        content = to_json(report)
        print(content)
        if args.json_out:
            write_json(args.json_out, content)
        return 0

    market = parse_market(args.market)

    if args.cmd == "backtest":
        result = PaperTrader(
            market,
            build_strategy(args.strategy),
            starting_cash=args.cash,
            store_path=args.json_out,
            config=_backtest_config(args),
        ).run(args.symbol, load_candles(args.data))
        print(backtest_console(result))
        return 0

    if args.cmd == "scan":
        model = CryptoSignalModel.load(args.model) if getattr(args, "model", None) and market == Market.CRYPTO else None
        results = (
            scan_crypto_folder(args.folder, top=args.top, model=model)
            if market == Market.CRYPTO
            else scan_equity_folder(args.folder, top=args.top)
        )
        print(scan_console(results))
        if args.json_out:
            write_json(args.json_out, to_json(results))
        return 0

    if args.cmd == "walk-forward":
        result = walk_forward(
            args.symbol,
            market,
            load_candles(args.data),
            strategy_name=args.strategy,
            train_size=args.train_size,
            test_size=args.test_size,
            backtest_config=_backtest_config(args),
        )
        print(walk_forward_console(result))
        if args.json_out:
            write_json(args.json_out, to_json(result))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
