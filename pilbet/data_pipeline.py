"""OHLCV ingestion, validation, and trailing SMA construction."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from pilbet.config import BotConfig
from pilbet.synthetic import generate_synthetic_ohlcv

LOGGER = logging.getLogger("pilbet.data")

REQUIRED_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")


def _clean_column_name(col: object) -> str:
    name = str(col).strip().strip("<>").strip().lower()
    return name.replace(" ", "_")


def _combine_mt5_datetime(frame: pd.DataFrame) -> pd.DataFrame:
    """MT5 history export uses separate DATE / TIME columns (2024.01.02 + 00:00)."""
    if "timestamp" in frame.columns:
        return frame
    if "date" in frame.columns and "time" in frame.columns:
        date = frame["date"].astype(str).str.replace(".", "-", regex=False)
        time = frame["time"].astype(str)
        out = frame.copy()
        out["timestamp"] = pd.to_datetime(date + " " + time, utc=False)
        return out
    if "date" in frame.columns:
        out = frame.copy()
        out["timestamp"] = pd.to_datetime(
            out["date"].astype(str).str.replace(".", "-", regex=False), utc=False
        )
        return out
    return frame


class DataPipeline:
    """Load candles, drop incomplete bars, and compute trailing SMAs.

    Indicators use a *backward-looking* rolling window (``center=False``).
    Signals must still be evaluated on two completed bars by the strategy;
    this class only produces the inputs.
    """

    def __init__(self, config: Optional[BotConfig] = None) -> None:
        self.config = config or BotConfig()

    def load_csv(self, path: str | Path) -> pd.DataFrame:
        LOGGER.info("Loading OHLCV from %s", path)
        frame = pd.read_csv(path, sep=None, engine="python")
        return self.prepare(frame)

    def load_synthetic(
        self,
        n_bars: int = 400,
        seed: int = 42,
        start_price: float = 100.0,
    ) -> pd.DataFrame:
        LOGGER.info(
            "Generating synthetic OHLCV bars=%s seed=%s start_price=%.4f",
            n_bars,
            seed,
            start_price,
        )
        frame = generate_synthetic_ohlcv(
            n_bars=n_bars, seed=seed, start_price=start_price
        )
        return self.prepare(frame)

    def prepare(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Normalize, optionally drop a forming last bar, then add SMAs."""
        out = self._normalize(frame)
        out = self._maybe_drop_incomplete(out)
        self.validate(out)
        out = self.compute_indicators(out)
        self._log_warmup(out)
        return out.reset_index(drop=True)

    def compute_indicators(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Trailing SMAs on close. Never use center=True (lookahead)."""
        out = frame.copy()
        short = self.config.short_window
        long = self.config.long_window
        out["sma_short"] = out["close"].rolling(window=short, min_periods=short).mean()
        out["sma_long"] = out["close"].rolling(window=long, min_periods=long).mean()
        return out

    def validate(self, frame: pd.DataFrame) -> None:
        missing = [col for col in REQUIRED_COLUMNS if col not in frame.columns]
        if missing:
            raise ValueError(f"OHLCV missing columns: {missing}")
        if frame.empty:
            raise ValueError("OHLCV frame is empty")
        if frame[list(REQUIRED_COLUMNS[1:])].isna().any().any():
            raise ValueError("OHLCV contains NaN prices or volume")
        if (frame[["open", "high", "low", "close"]] <= 0).any().any():
            raise ValueError("OHLCV prices must be positive")
        if (frame["volume"] < 0).any():
            raise ValueError("volume cannot be negative")

        timestamps = pd.to_datetime(frame["timestamp"], utc=False)
        if not timestamps.is_monotonic_increasing:
            raise ValueError("timestamps must be sorted ascending")
        if timestamps.duplicated().any():
            raise ValueError("duplicate timestamps are not allowed")

        high_ok = frame["high"] + 1e-12 >= frame[["open", "close"]].max(axis=1)
        low_ok = frame["low"] - 1e-12 <= frame[["open", "close"]].min(axis=1)
        if not bool(high_ok.all() and low_ok.all()):
            raise ValueError("high/low do not bound open/close on every bar")

    def _normalize(self, frame: pd.DataFrame) -> pd.DataFrame:
        out = frame.copy()
        out.columns = [_clean_column_name(col) for col in out.columns]
        out = _combine_mt5_datetime(out)
        if "volume" not in out.columns:
            for alias in ("tickvol", "tick_volume", "real_volume", "vol"):
                if alias in out.columns:
                    out["volume"] = out[alias]
                    break
            else:
                out["volume"] = 1.0
        rename = {"datetime": "timestamp"}
        out = out.rename(columns=rename)
        if "timestamp" not in out.columns:
            if isinstance(out.index, pd.DatetimeIndex):
                out = out.reset_index().rename(columns={"index": "timestamp"})
            else:
                raise ValueError("frame must include a timestamp column")
        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=False)
        for col in ("open", "high", "low", "close", "volume"):
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="raise")
        if "is_closed" in out.columns:
            out["is_closed"] = out["is_closed"].astype(bool)
        return out.sort_values("timestamp").reset_index(drop=True)

    def _maybe_drop_incomplete(self, frame: pd.DataFrame) -> pd.DataFrame:
        if not self.config.drop_incomplete_last_bar or frame.empty:
            return frame

        last = frame.iloc[-1]
        incomplete = False
        if "is_closed" in frame.columns and not bool(last["is_closed"]):
            incomplete = True
        if not self.config.last_bar_closed:
            incomplete = True

        if incomplete:
            LOGGER.warning(
                "Dropping incomplete last bar ts=%s (signals use completed candles only)",
                last["timestamp"],
            )
            return frame.iloc[:-1].copy()
        return frame

    def _log_warmup(self, frame: pd.DataFrame) -> None:
        valid = frame["sma_long"].first_valid_index()
        if valid is None:
            LOGGER.warning("SMA warmup incomplete: not enough bars for long window")
            return
        row = frame.loc[valid]
        LOGGER.info(
            "Indicators ready symbol=%s bars=%s short=%s long=%s "
            "first_valid_ts=%s sma_short=%.4f sma_long=%.4f",
            self.config.symbol,
            len(frame),
            self.config.short_window,
            self.config.long_window,
            row["timestamp"],
            row["sma_short"],
            row["sma_long"],
        )
