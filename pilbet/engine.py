"""Bar-by-bar orchestrator: signal on close, fill on next open, manage SL/TP."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from pilbet.config import BotConfig
from pilbet.data_pipeline import DataPipeline
from pilbet.metrics import PerformanceReport, build_report
from pilbet.models import (
    Candle,
    EquityPoint,
    ExitReason,
    OrderKind,
    PendingOrder,
    Side,
    Signal,
    Trade,
)
from pilbet.portfolio import Portfolio
from pilbet.risk_manager import RiskManager
from pilbet.strategy import SMACrossoverStrategy

LOGGER = logging.getLogger("pilbet.engine")

_ENTRY_KINDS = {OrderKind.ENTRY_LONG, OrderKind.REVERSE_TO_LONG}
_SHORT_KINDS = {OrderKind.ENTRY_SHORT, OrderKind.REVERSE_TO_SHORT}
_FLATTEN_KINDS = {
    OrderKind.EXIT_REVERSAL,
    OrderKind.REVERSE_TO_LONG,
    OrderKind.REVERSE_TO_SHORT,
}


@dataclass
class BacktestResult:
    candles: pd.DataFrame
    equity_curve: list[EquityPoint]
    trades: list[Trade]
    report: PerformanceReport
    config: BotConfig
    pending_unfilled: Optional[PendingOrder] = None

    def trades_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame()
        return pd.DataFrame(
            [
                {
                    "entry_time": t.entry_time,
                    "exit_time": t.exit_time,
                    "side": t.side.value,
                    "quantity": t.quantity,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "pnl": t.pnl,
                    "r_multiple": t.r_multiple,
                    "reason": t.reason.value,
                    "entry_fee": t.entry_fee,
                    "exit_fee": t.exit_fee,
                }
                for t in self.trades
            ]
        )

    def equity_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "timestamp": p.timestamp,
                    "cash": p.cash,
                    "equity": p.equity,
                    "unrealized_pnl": p.unrealized_pnl,
                    "realized_pnl": p.realized_pnl,
                    "in_position": p.in_position,
                }
                for p in self.equity_curve
            ]
        )

    def save_reports(self, directory: Optional[Path] = None) -> Path:
        out_dir = Path(directory or self.config.reports_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        self.trades_frame().to_csv(out_dir / "trades.csv", index=False)
        self.equity_frame().to_csv(out_dir / "equity.csv", index=False)
        pd.DataFrame([self.report.as_dict()]).to_csv(
            out_dir / "summary.csv", index=False
        )
        _plot_backtest(self, out_dir / "equity_curve.png")
        LOGGER.info("Wrote reports to %s", out_dir.resolve())
        return out_dir


class BacktestEngine:
    """Causal event loop.

    For each completed bar ``i``:

    1. Fill any order queued from bar ``i-1`` at this bar's *open* (plus spread).
    2. Check gap-through and intra-bar SL/TP on the open position.
    3. Mark equity at the close.
    4. Evaluate the 20/50 cross on ``iloc[-2]`` vs ``iloc[-1]`` of ``history[:i+1]``.
    5. Queue the resulting order for bar ``i+1`` open — never trade the close
       you just used to compute the SMA.
    """

    def __init__(
        self,
        config: Optional[BotConfig] = None,
        *,
        pipeline: Optional[DataPipeline] = None,
        strategy: Optional[SMACrossoverStrategy] = None,
        risk: Optional[RiskManager] = None,
        portfolio: Optional[Portfolio] = None,
    ) -> None:
        self.config = config or BotConfig()
        self.pipeline = pipeline or DataPipeline(self.config)
        self.strategy = strategy or SMACrossoverStrategy(self.config)
        self.risk = risk or RiskManager(self.config)
        self.portfolio = portfolio or Portfolio(self.config)
        self.pending: Optional[PendingOrder] = None
        self.equity_curve: list[EquityPoint] = []

    def run(self, candles: pd.DataFrame) -> BacktestResult:
        frame = candles.copy()
        if "sma_long" not in frame.columns:
            frame = self.pipeline.prepare(frame)
        else:
            frame = frame.reset_index(drop=True)

        LOGGER.info(
            "Starting backtest symbol=%s tf=%s bars=%s cash=%.2f risk=%.2f%% "
            "sl=%.1fp tp=%.1fp spread=%.1fp fee=%.4f%% shorts=%s margin=%s",
            self.config.symbol,
            self.config.timeframe,
            len(frame),
            self.config.starting_cash,
            self.config.risk_fraction * 100.0,
            self.config.stop_loss_pips,
            self.config.take_profit_pips,
            self.config.spread_pips,
            self.config.fee_rate * 100.0,
            self.config.allow_short,
            self.config.use_margin,
        )

        for i in range(len(frame)):
            self.step(frame, i)

        if self.config.flatten_at_end and self.portfolio.has_position:
            last = _candle_at(frame, len(frame) - 1)
            is_buy = self.portfolio.position.side is Side.SHORT
            fill = self.portfolio.apply_fill_price(last.close, is_buy=is_buy)
            self.portfolio.close_position(
                candle=last, fill_price=fill, reason=ExitReason.END_OF_DATA
            )

        report = build_report(
            starting_cash=self.config.starting_cash,
            equity_curve=self.equity_curve,
            trades=self.portfolio.trades,
            fees_paid=self.portfolio.fees_paid,
            ambiguous_exits=self.portfolio.ambiguous_exits,
        )
        LOGGER.info(
            "Backtest complete equity=%.2f return=%.2f%% trades=%s win_rate=%.1f%% "
            "max_dd=%.2f%% sharpe=%.2f ambiguous_exits=%s",
            report.ending_equity,
            report.total_return_pct,
            report.trade_count,
            report.win_rate_pct,
            report.max_drawdown_pct,
            report.sharpe,
            report.ambiguous_exits,
        )
        return BacktestResult(
            candles=frame,
            equity_curve=self.equity_curve,
            trades=self.portfolio.trades,
            report=report,
            config=self.config,
            pending_unfilled=self.pending,
        )

    def step(self, frame: pd.DataFrame, index: int) -> None:
        candle = _candle_at(frame, index)
        self._process_open(candle)
        self._process_intrabar_exits(candle)
        self._mark(candle)
        self._queue_signal(frame, index)

    def _process_open(self, candle: Candle) -> None:
        if self.portfolio.has_position:
            gap = self.portfolio.resolve_gap_exit(candle)
            if gap is not None:
                fill, reason = gap
                is_buy = self.portfolio.position.side is Side.SHORT
                fill = self.portfolio.apply_fill_price(fill, is_buy=is_buy)
                self.portfolio.close_position(
                    candle=candle, fill_price=fill, reason=reason
                )
                self.pending = _cancel_flatten(self.pending)

        if (
            self.pending
            and self.pending.kind in _FLATTEN_KINDS
            and self.portfolio.has_position
        ):
            is_buy = self.portfolio.position.side is Side.SHORT
            fill = self.portfolio.apply_fill_price(candle.open, is_buy=is_buy)
            self.portfolio.close_position(
                candle=candle,
                fill_price=fill,
                reason=ExitReason.STRATEGIC_REVERSAL,
            )

        if self.pending and self.pending.kind in _ENTRY_KINDS | _SHORT_KINDS:
            if not self.portfolio.has_position:
                side = Side.LONG if self.pending.kind in _ENTRY_KINDS else Side.SHORT
                self._fill_entry(candle, side)
            self.pending = None
        elif self.pending and self.pending.kind == OrderKind.EXIT_REVERSAL:
            self.pending = None

    def _fill_entry(self, candle: Candle, side: Side) -> None:
        if not self.risk.can_enter(self.portfolio.position):
            LOGGER.info("Suppressing entry; a position is already open")
            return
        is_buy = side is Side.LONG
        fill = self.portfolio.apply_fill_price(candle.open, is_buy=is_buy)
        equity = self.portfolio.mark_equity(fill)
        sizing = self.risk.size(
            side=side,
            equity=equity,
            cash=self.portfolio.cash,
            entry_price=fill,
        )
        if not sizing.accepted:
            return
        self.portfolio.open_position(
            side=side, candle=candle, fill_price=fill, sizing=sizing
        )

    def _process_intrabar_exits(self, candle: Candle) -> None:
        if not self.portfolio.has_position:
            return
        resolved = self.portfolio.resolve_intrabar_exit(candle)
        if resolved is None:
            return
        fill, reason = resolved
        is_buy = self.portfolio.position.side is Side.SHORT
        fill = self.portfolio.apply_fill_price(fill, is_buy=is_buy)
        self.portfolio.close_position(candle=candle, fill_price=fill, reason=reason)
        self.pending = _cancel_flatten(self.pending)

    def _mark(self, candle: Candle) -> None:
        equity = self.portfolio.mark_equity(candle.close)
        self.equity_curve.append(
            EquityPoint(
                index=candle.index,
                timestamp=candle.timestamp,
                cash=self.portfolio.cash,
                equity=equity,
                unrealized_pnl=self.portfolio.unrealized_pnl(candle.close),
                realized_pnl=self.portfolio.realized_pnl,
                in_position=self.portfolio.has_position,
            )
        )

    def _queue_signal(self, frame: pd.DataFrame, index: int) -> None:
        history = frame.iloc[: index + 1]
        signal = self.strategy.evaluate(history)
        last = history.iloc[-1]
        pos = self.portfolio.position if self.portfolio.has_position else None

        if signal == Signal.GOLDEN_CROSS:
            self._queue_bullish(index, last, pos, signal)
            return
        if signal == Signal.DEATH_CROSS:
            self._queue_bearish(index, last, pos, signal)

    def _queue_bullish(self, index: int, last, pos, signal: Signal) -> None:
        if pos is not None and pos.side is Side.LONG:
            LOGGER.info("Suppressing GOLDEN_CROSS; already long")
            return
        if self.pending is not None and self.pending.kind in _ENTRY_KINDS:
            LOGGER.info("Suppressing GOLDEN_CROSS; long entry already pending")
            return
        if pos is not None and pos.side is Side.SHORT:
            kind = OrderKind.REVERSE_TO_LONG
        else:
            kind = OrderKind.ENTRY_LONG
        self.pending = _pending(kind, signal, index, last)
        LOGGER.info("Queued %s for next open after ts=%s", kind.value, last["timestamp"])

    def _queue_bearish(self, index: int, last, pos, signal: Signal) -> None:
        if pos is not None and pos.side is Side.SHORT:
            LOGGER.info("Suppressing DEATH_CROSS; already short")
            return
        if pos is None and not self.config.allow_short:
            return
        if pos is not None and pos.side is Side.LONG and not self.config.allow_short:
            kind = OrderKind.EXIT_REVERSAL
        elif pos is not None and pos.side is Side.LONG:
            kind = OrderKind.REVERSE_TO_SHORT
        elif self.config.allow_short:
            if self.pending is not None and self.pending.kind in _SHORT_KINDS:
                LOGGER.info("Suppressing DEATH_CROSS; short entry already pending")
                return
            kind = OrderKind.ENTRY_SHORT
        else:
            return
        self.pending = _pending(kind, signal, index, last)
        LOGGER.info("Queued %s for next open after ts=%s", kind.value, last["timestamp"])


def _pending(kind: OrderKind, signal: Signal, index: int, last) -> PendingOrder:
    return PendingOrder(
        kind=kind,
        signal=signal,
        signal_index=index,
        signal_time=last["timestamp"].to_pydatetime(),
        sma_short=float(last["sma_short"]),
        sma_long=float(last["sma_long"]),
    )


def _cancel_flatten(pending: Optional[PendingOrder]) -> Optional[PendingOrder]:
    if pending is not None and pending.kind in _FLATTEN_KINDS:
        return None
    return pending


def _candle_at(frame: pd.DataFrame, index: int) -> Candle:
    row = frame.iloc[index]
    sma_short = row["sma_short"] if "sma_short" in frame.columns else None
    sma_long = row["sma_long"] if "sma_long" in frame.columns else None
    return Candle(
        index=index,
        timestamp=row["timestamp"].to_pydatetime(),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        sma_short=None if pd.isna(sma_short) else float(sma_short),
        sma_long=None if pd.isna(sma_long) else float(sma_long),
    )


def _plot_backtest(result: BacktestResult, path: Path) -> None:
    import os

    os.environ.setdefault("MPLCONFIGDIR", str(path.parent / ".mplconfig"))
    path.parent.joinpath(".mplconfig").mkdir(parents=True, exist_ok=True)

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    price = result.candles
    equity = result.equity_frame()
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=False)

    axes[0].plot(price["timestamp"], price["close"], color="#1f77b4", lw=1.0, label="Close")
    if "sma_short" in price.columns:
        axes[0].plot(
            price["timestamp"],
            price["sma_short"],
            color="#ff7f0e",
            lw=1.0,
            label=f"SMA {result.config.short_window}",
        )
    if "sma_long" in price.columns:
        axes[0].plot(
            price["timestamp"],
            price["sma_long"],
            color="#2ca02c",
            lw=1.0,
            label=f"SMA {result.config.long_window}",
        )
    longs = [t for t in result.trades if t.side is Side.LONG]
    shorts = [t for t in result.trades if t.side is Side.SHORT]
    if longs:
        axes[0].scatter(
            [t.entry_time for t in longs],
            [t.entry_price for t in longs],
            marker="^",
            color="green",
            s=28,
            zorder=3,
            label="Long",
        )
    if shorts:
        axes[0].scatter(
            [t.entry_time for t in shorts],
            [t.entry_price for t in shorts],
            marker="v",
            color="purple",
            s=28,
            zorder=3,
            label="Short",
        )
    if result.trades:
        axes[0].scatter(
            [t.exit_time for t in result.trades],
            [t.exit_price for t in result.trades],
            marker="x",
            color="red",
            s=28,
            zorder=3,
            label="Exit",
        )
    axes[0].set_title(f"{result.config.symbol} SMA crossover ({result.config.timeframe})")
    axes[0].legend(loc="upper left", fontsize=8)
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(equity["timestamp"], equity["equity"], color="#9467bd", lw=1.2)
    axes[1].axhline(result.config.starting_cash, color="gray", ls="--", lw=0.8)
    axes[1].set_title("Equity")
    axes[1].grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
