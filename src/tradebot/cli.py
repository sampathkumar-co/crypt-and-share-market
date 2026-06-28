from __future__ import annotations

import argparse
from pathlib import Path

from tradebot.backtest.paper_live import PaperLiveCryptoBot
from tradebot.backtest.paper_trader import PaperTrader
from tradebot.backtest.ml_comparison import compare_crypto_ml
from tradebot.backtest.portfolio_trader import CryptoPortfolioPaperTrader, PortfolioConfig
from tradebot.backtest.robustness import evaluate_robustness
from tradebot.backtest.walk_forward import walk_forward
from tradebot.api.server import run_server
from tradebot.data.csv_loader import load_candles
from tradebot.data.crypto_provider import PublicCryptoHistoricalClient
from tradebot.ml.crypto_signal_model import CryptoSignalModel, evaluate_folder, train_from_folder
from tradebot.models import Market
from tradebot.reports.demo_report import generate_demo_report
from tradebot.reports.report_generator import backtest_console, ml_comparison_console, portfolio_console, robustness_console, scan_console, to_json, walk_forward_console
from tradebot.scanner.crypto_scanner import scan_crypto_folder
from tradebot.scanner.equity_scanner import scan_equity_folder
from tradebot.strategies.momentum import MomentumVolumeStrategy


def parse_market(value: str) -> Market:
    return Market(value.lower())


def write_json(path: str, content: str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="Dual Market AI Bot",
        description="Safe paper-trading research CLI. No real orders.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    backtest_parser = sub.add_parser("backtest")
    backtest_parser.add_argument("--market", required=True)
    backtest_parser.add_argument("--symbol", required=True)
    backtest_parser.add_argument("--data", required=True)
    backtest_parser.add_argument("--json-out")

    scan_parser = sub.add_parser("scan")
    scan_parser.add_argument("--market", required=True)
    scan_parser.add_argument("--folder", required=True)
    scan_parser.add_argument("--json-out")
    scan_parser.add_argument("--top", type=int, default=None)
    scan_parser.add_argument("--model")

    walk_parser = sub.add_parser("walk-forward")
    walk_parser.add_argument("--market", required=True)
    walk_parser.add_argument("--symbol", required=True)
    walk_parser.add_argument("--data", required=True)
    walk_parser.add_argument("--strategy", choices=["momentum", "breakout", "mean_reversion"], default="momentum")
    walk_parser.add_argument("--json-out")

    fetch_parser = sub.add_parser("fetch-crypto")
    fetch_parser.add_argument("--symbols", required=True, help="Comma-separated symbols, for example BTCUSDT,ETHUSDT")
    fetch_parser.add_argument("--interval", default="1d")
    fetch_parser.add_argument("--days", type=int, default=365)
    fetch_parser.add_argument("--out", default="data/crypto")

    portfolio_parser = sub.add_parser("portfolio-crypto")
    portfolio_parser.add_argument("--folder", required=True)
    portfolio_parser.add_argument("--cash", type=float, default=100000.0)
    portfolio_parser.add_argument("--top", type=int, default=20)
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
    paper_live_parser.add_argument("--max-loops", type=int, default=1)
    paper_live_parser.add_argument("--sleep-seconds", type=float, default=60.0)

    dashboard_parser = sub.add_parser("serve-dashboard")
    dashboard_parser.add_argument("--host", default="127.0.0.1")
    dashboard_parser.add_argument("--port", type=int, default=8000)

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
        run_server(args.host, args.port)
        return 0

    if args.cmd == "paper-live-crypto":
        model = CryptoSignalModel.load(args.model) if args.model else None
        symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
        bot = PaperLiveCryptoBot(symbols, args.interval, args.cash, args.state, model=model)
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
        result = CryptoPortfolioPaperTrader(cash=args.cash, config=PortfolioConfig(scanner_top=args.top), model=model).run_folder(args.folder)
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

    market = parse_market(args.market)

    if args.cmd == "backtest":
        result = PaperTrader(market, MomentumVolumeStrategy(), store_path=args.json_out).run(
            args.symbol,
            load_candles(args.data),
        )
        print(backtest_console(result))
        return 0

    if args.cmd == "scan":
        model = CryptoSignalModel.load(args.model) if getattr(args, "model", None) and market == Market.CRYPTO else None
        results = scan_crypto_folder(args.folder, top=args.top, model=model) if market == Market.CRYPTO else scan_equity_folder(args.folder, top=args.top)
        print(scan_console(results))
        if args.json_out:
            write_json(args.json_out, to_json(results))
        return 0

    if args.cmd == "walk-forward":
        result = walk_forward(args.symbol, market, load_candles(args.data), strategy_name=args.strategy)
        print(walk_forward_console(result))
        if args.json_out:
            write_json(args.json_out, to_json(result))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
