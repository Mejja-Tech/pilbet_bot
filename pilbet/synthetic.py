"""Synthetic OHLCV with regime shifts so the SMA crossover actually fires."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd


def generate_synthetic_ohlcv(
    n_bars: int = 400,
    seed: int = 42,
    start_price: float = 100.0,
    start: datetime | None = None,
    freq: str = "D",
) -> pd.DataFrame:
    """Build a reproducible daily (or generic) OHLC series.

    A single geometric random walk with constant drift rarely produces clean
    20/50 crosses. We therefore switch drift across a few regimes so the
    execution loop can be inspected with real entries and exits.
    """
    if n_bars < 80:
        raise ValueError("n_bars must be >= 80 so the 50-period SMA can warm up")

    rng = np.random.default_rng(seed)
    start = start or datetime(2022, 1, 3, 9, 30)
    timestamps = pd.date_range(start=start, periods=n_bars, freq=freq)

    # Drift regimes: chop, bull, chop, bear, bull.
    # Daily vol is kept below the 1.5% stop so the demo can actually reach
    # take-profit / reversal instead of getting wick-stopped on every entry.
    cuts = np.array_split(np.arange(n_bars), 5)
    drifts = np.zeros(n_bars)
    vols = np.full(n_bars, 0.006)
    drift_vol = (
        (0.0001, 0.0055),
        (0.0018, 0.0060),
        (0.0000, 0.0050),
        (-0.0015, 0.0070),
        (0.0014, 0.0058),
    )
    for chunk, (mu, sigma) in zip(cuts, drift_vol):
        drifts[chunk] = mu
        vols[chunk] = sigma

    log_rets = rng.normal(drifts, vols)
    close = start_price * np.exp(np.cumsum(log_rets))
    floor = start_price * 0.2
    close = np.maximum(close, floor)

    prev_close = np.concatenate([[start_price], close[:-1]])
    gap = rng.normal(0.0, 0.0008, size=n_bars)
    open_ = np.maximum(prev_close * (1.0 + gap), floor)
    wick = np.abs(rng.normal(0.0010, 0.0006, size=n_bars))
    high = np.maximum(open_, close) * (1.0 + wick)
    low = np.minimum(open_, close) * (1.0 - wick)
    low = np.maximum(low, floor * 0.5)
    volume = rng.integers(80_000, 250_000, size=n_bars).astype(float)

    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "is_closed": True,
        }
    )
    return _repair_ohlc(frame)


def _repair_ohlc(frame: pd.DataFrame) -> pd.DataFrame:
    """Guarantee high/low bound open and close (synthetic noise can violate this)."""
    out = frame.copy()
    out["high"] = out[["open", "high", "close"]].max(axis=1)
    out["low"] = out[["open", "low", "close"]].min(axis=1)
    return out
