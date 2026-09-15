"""Performance statistics computed after a backtest run."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from pilbet.models import EquityPoint, Trade


@dataclass(frozen=True)
class PerformanceReport:
    starting_cash: float
    ending_equity: float
    total_return_pct: float
    realized_pnl: float
    fees_paid: float
    trade_count: int
    win_rate_pct: float
    profit_factor: float
    average_trade_pnl: float
    max_drawdown_pct: float
    sharpe: float
    ambiguous_exits: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "starting_cash": self.starting_cash,
            "ending_equity": self.ending_equity,
            "total_return_pct": self.total_return_pct,
            "realized_pnl": self.realized_pnl,
            "fees_paid": self.fees_paid,
            "trade_count": self.trade_count,
            "win_rate_pct": self.win_rate_pct,
            "profit_factor": self.profit_factor,
            "average_trade_pnl": self.average_trade_pnl,
            "max_drawdown_pct": self.max_drawdown_pct,
            "sharpe": self.sharpe,
            "ambiguous_exits": self.ambiguous_exits,
        }


def build_report(
    *,
    starting_cash: float,
    equity_curve: list[EquityPoint],
    trades: list[Trade],
    fees_paid: float,
    ambiguous_exits: int,
) -> PerformanceReport:
    ending = equity_curve[-1].equity if equity_curve else starting_cash
    total_return = (ending / starting_cash - 1.0) * 100.0 if starting_cash else 0.0
    pnls = [trade.pnl for trade in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = (len(wins) / len(pnls) * 100.0) if pnls else 0.0
    gross_win = float(sum(wins))
    gross_loss = float(abs(sum(losses)))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win else 0.0
    avg = float(np.mean(pnls)) if pnls else 0.0

    equity = pd.Series([point.equity for point in equity_curve], dtype=float)
    max_dd = _max_drawdown_pct(equity)
    sharpe = _sharpe(equity, equity_curve)

    return PerformanceReport(
        starting_cash=starting_cash,
        ending_equity=ending,
        total_return_pct=total_return,
        realized_pnl=sum(pnls),
        fees_paid=fees_paid,
        trade_count=len(trades),
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        average_trade_pnl=avg,
        max_drawdown_pct=max_dd,
        sharpe=sharpe,
        ambiguous_exits=ambiguous_exits,
    )


def _max_drawdown_pct(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity / peak) - 1.0
    return float(dd.min() * 100.0)


def _sharpe(equity: pd.Series, curve: list[EquityPoint]) -> float:
    if len(equity) < 3:
        return 0.0
    rets = equity.pct_change().dropna()
    rets = rets[np.isfinite(rets)]
    if rets.std(ddof=1) == 0 or rets.empty:
        return 0.0
    periods = _periods_per_year(curve)
    return float(np.sqrt(periods) * rets.mean() / rets.std(ddof=1))


def _periods_per_year(curve: list[EquityPoint]) -> float:
    if len(curve) < 2:
        return 252.0
    delta = (curve[1].timestamp - curve[0].timestamp).total_seconds()
    if delta <= 0:
        return 252.0
    seconds_year = 365.25 * 24 * 3600
    return seconds_year / delta
