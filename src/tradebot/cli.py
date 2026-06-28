from __future__ import annotations

import argparse
from pathlib import Path

from tradebot.backtest.paper_trader import PaperTrader
from tradebot.backtest.portfolio_trader import CryptoPortfolioPaperTrader, PortfolioConfig
from tradebot.backtest.robustness import evaluate_robustness
from tradebot.backtest.walk_forward import walk_forward
from tradebot.data.csv_loader import load_candles
from tradebot.data.crypto_provider import PublicCryptoHistoricalClient
from tradebot.ml.crypto_signal_model import CryptoSignalModel, evaluate_folder, train_from_folder
from tradebot.models import Market
from tradebot.reports.report_generator import backtest_console, portfolio_console, robustness_console, scan_console, to_json, walk_forward_console
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

    robustness_parser = sub.add_parser("robustness-crypto")
    robustness_parser.add_argument("--folder", required=True)
    robustness_parser.add_argument("--cash", type=float, default=100000.0)
    robustness_parser.add_argument("--json-out")

    train_ml_parser = sub.add_parser("train-crypto-ml")
    train_ml_parser.add_argument("--folder", required=True)
    train_ml_parser.add_argument("--model-out", required=True)

    eval_ml_parser = sub.add_parser("evaluate-crypto-ml")
    eval_ml_parser.add_argument("--folder", required=True)
    eval_ml_parser.add_argument("--model", required=True)
    eval_ml_parser.add_argument("--json-out")

    args = parser.parse_args(argv)
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
        result = evaluate_robustness(args.folder, cash=args.cash)
        print(robustness_console(result))
        if args.json_out:
            write_json(args.json_out, to_json(result))
        return 0

    if args.cmd == "portfolio-crypto":
        result = CryptoPortfolioPaperTrader(cash=args.cash, config=PortfolioConfig(scanner_top=args.top)).run_folder(args.folder)
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
