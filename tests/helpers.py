"""Shared candle-frame builders for unit tests."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from pilbet.config import BotConfig
from pilbet.models import Candle, Side, SizingDecision
from pilbet.portfolio import Portfolio


def spot_config(**overrides) -> BotConfig:
    """Percent-stop, cash-notional book used by the original unit tests."""
    kwargs = dict(
        symbol="SYNTH",
        stop_loss_pips=0.0,
        take_profit_pips=0.0,
        stop_loss_pct=0.015,
        take_profit_pct=0.030,
        pip_size=0.01,
        contract_size=1.0,
        spread_pips=0.0,
        slippage_pips=0.0,
        use_margin=False,
        allow_short=False,
        volume_min=0.0,
        volume_step=0.0,
        volume_max=1_000_000.0,
        leverage=1.0,
        fee_rate=0.0005,
        quantity_precision=8,
        min_notional=10.0,
    )
    kwargs.update(overrides)
    return BotConfig(**kwargs)


def ohlc_frame(
    n: int = 60,
    price: float = 100.0,
    start: str = "2024-01-02",
) -> pd.DataFrame:
    timestamps = pd.date_range(start=start, periods=n, freq="D")
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": price,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price,
            "volume": 1000.0,
            "sma_short": 99.0,
            "sma_long": 100.0,
            "is_closed": True,
        }
    )


def paint_golden_cross(frame: pd.DataFrame, at: int) -> pd.DataFrame:
    """Make SMA20 cross above SMA50 on bar ``at`` (iloc[-1] vs previous)."""
    out = frame.copy()
    out.loc[: at - 1, "sma_short"] = 99.0
    out.loc[: at - 1, "sma_long"] = 100.0
    out.loc[at:, "sma_short"] = 101.0
    out.loc[at:, "sma_long"] = 100.0
    return out


def paint_death_cross(frame: pd.DataFrame, at: int) -> pd.DataFrame:
    out = frame.copy()
    out.loc[: at - 1, "sma_short"] = 101.0
    out.loc[: at - 1, "sma_long"] = 100.0
    out.loc[at:, "sma_short"] = 99.0
    out.loc[at:, "sma_long"] = 100.0
    return out


def candle_from_row(frame: pd.DataFrame, index: int) -> Candle:
    row = frame.iloc[index]
    return Candle(
        index=index,
        timestamp=row["timestamp"].to_pydatetime()
        if hasattr(row["timestamp"], "to_pydatetime")
        else datetime.fromisoformat(str(row["timestamp"])),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        sma_short=float(row["sma_short"]),
        sma_long=float(row["sma_long"]),
    )


def force_long(
    portfolio: Portfolio,
    candle: Candle,
    quantity: float = 10.0,
    entry_price: float | None = None,
) -> None:
    price = entry_price if entry_price is not None else candle.open
    sl, tp = portfolio.config.long_brackets(price)
    sizing = SizingDecision(
        quantity=quantity,
        side=Side.LONG,
        stop_loss=sl,
        take_profit=tp,
        risk_dollars=portfolio.config.starting_cash * portfolio.config.risk_fraction,
        notional=quantity * price,
        constraint="test",
    )
    portfolio.open_long(candle=candle, fill_price=price, sizing=sizing)


def force_short(
    portfolio: Portfolio,
    candle: Candle,
    quantity: float = 10.0,
    entry_price: float | None = None,
) -> None:
    price = entry_price if entry_price is not None else candle.open
    sl, tp = portfolio.config.short_brackets(price)
    sizing = SizingDecision(
        quantity=quantity,
        side=Side.SHORT,
        stop_loss=sl,
        take_profit=tp,
        risk_dollars=portfolio.config.starting_cash * portfolio.config.risk_fraction,
        notional=quantity * price,
        constraint="test",
    )
    portfolio.open_short(candle=candle, fill_price=price, sizing=sizing)
