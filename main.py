#!/usr/bin/env python3
"""Run the SMA-crossover execution loop on synthetic, CSV, or MT5 history."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from pilbet.config import BotConfig
from pilbet.data_pipeline import DataPipeline
from pilbet.engine import BacktestEngine
from pilbet.fx import infer_synthetic_price
from pilbet.logging_setup import setup_logging
from pilbet.mt5_client import MT5Client, MT5Credentials, MT5Unavailable


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FXPesa/MT5 XAUUSD SMA 20/50 crossover backtest"
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="OHLCV CSV (plain or MT5 history export)",
    )
    parser.add_argument(
        "--mt5",
        action="store_true",
        help="Pull history from a running FXPesa MT5 terminal (Windows only)",
    )
    parser.add_argument("--bars", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--cash", type=float, default=10_000.0)
    parser.add_argument("--symbol", type=str, default="XAUUSD")
    parser.add_argument("--timeframe", type=str, default="H1")
    parser.add_argument(
        "--sl-pips",
        type=float,
        default=None,
        help="Stop loss in pips (default: 400 on XAUUSD, 50 on FX)",
    )
    parser.add_argument(
        "--tp-pips",
        type=float,
        default=None,
        help="Take profit in pips (default: 800 on XAUUSD, 100 on FX)",
    )
    parser.add_argument(
        "--spread-pips",
        type=float,
        default=None,
        help="Half-spread model in pips (default: 30 on XAUUSD)",
    )
    parser.add_argument("--no-short", action="store_true")
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Skip writing reports/equity_curve.png",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = BotConfig(
        symbol=args.symbol,
        timeframe=args.timeframe.upper(),
        starting_cash=args.cash,
        stop_loss_pips=args.sl_pips,
        take_profit_pips=args.tp_pips,
        spread_pips=args.spread_pips,
        allow_short=not args.no_short,
        reports_dir=Path("reports"),
        log_file=Path("logs/pilbet.log"),
    )
    setup_logging(
        level=logging.DEBUG if args.verbose else logging.INFO,
        log_file=config.log_file,
    )

    pipeline = DataPipeline(config)
    if args.mt5:
        candles = _load_mt5(config, args.bars)
        candles = pipeline.prepare(candles)
    elif args.csv is not None:
        candles = pipeline.load_csv(args.csv)
    else:
        start_price = infer_synthetic_price(config.symbol) if config.use_margin else 100.0
        candles = pipeline.load_synthetic(
            n_bars=args.bars, seed=args.seed, start_price=start_price
        )

    engine = BacktestEngine(config, pipeline=pipeline)
    result = engine.run(candles)
    _print_summary(result.report)
    if not args.no_plot:
        result.save_reports()


def _load_mt5(config: BotConfig, bars: int):
    client = MT5Client(MT5Credentials.from_env())
    try:
        client.connect()
        return client.copy_rates(config.symbol, config.timeframe, bars)
    except MT5Unavailable as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        client.shutdown()


def _print_summary(report) -> None:
    print("\n=== SMA Crossover Backtest ===")
    rows = [
        ("Starting cash", f"${report.starting_cash:,.2f}"),
        ("Ending equity", f"${report.ending_equity:,.2f}"),
        ("Total return", f"{report.total_return_pct:.2f}%"),
        ("Realized PnL", f"${report.realized_pnl:,.2f}"),
        ("Fees paid", f"${report.fees_paid:,.2f}"),
        ("Trades", f"{report.trade_count}"),
        ("Win rate", f"{report.win_rate_pct:.1f}%"),
        ("Profit factor", f"{report.profit_factor:.2f}"),
        ("Avg trade PnL", f"${report.average_trade_pnl:,.2f}"),
        ("Max drawdown", f"{report.max_drawdown_pct:.2f}%"),
        ("Sharpe (ann.)", f"{report.sharpe:.2f}"),
        ("Ambiguous SL/TP bars", f"{report.ambiguous_exits}"),
    ]
    width = max(len(name) for name, _ in rows)
    for name, value in rows:
        print(f"  {name:<{width}}  {value}")
    print()


if __name__ == "__main__":
    main()
