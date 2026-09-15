"""Optional MetaTrader 5 terminal bridge for FXPesa history (and demo orders).

The official ``MetaTrader5`` package talks to a running MT5 terminal over a
local IPC channel. That terminal — and this package — are Windows-only.
On Linux this module raises ``MT5Unavailable`` with export instructions.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd

LOGGER = logging.getLogger("pilbet.mt5")

TIMEFRAME_MINUTES = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}


class MT5Unavailable(RuntimeError):
    """Raised when the terminal or the Python package cannot be used."""


@dataclass
class MT5Credentials:
    login: Optional[int] = None
    password: Optional[str] = None
    server: Optional[str] = None
    path: Optional[str] = None

    @classmethod
    def from_env(cls) -> "MT5Credentials":
        login = os.environ.get("MT5_LOGIN") or os.environ.get("FXPESA_LOGIN")
        return cls(
            login=int(login) if login else None,
            password=os.environ.get("MT5_PASSWORD") or os.environ.get("FXPESA_PASSWORD"),
            server=os.environ.get("MT5_SERVER") or os.environ.get("FXPESA_SERVER"),
            path=os.environ.get("MT5_PATH"),
        )


def _import_mt5():
    try:
        import MetaTrader5 as mt5  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on OS/package
        raise MT5Unavailable(
            "The MetaTrader5 Python package is not available. It only works on "
            "Windows next to a running MT5 terminal.\n\n"
            "On Linux (this machine): export history from FXPesa MT5 instead:\n"
            "  1. Open MT5, chart the symbol (e.g. XAUUSD) on your timeframe.\n"
            "  2. Right-click the chart → View → Tools → History Center, or\n"
            "     right-click Market Watch → Symbols → Bars → Export.\n"
            "  3. Save CSV and run: python main.py --csv that_file.csv --symbol XAUUSD\n\n"
            "To trade live, copy mql5/PilbetSMA.mq5 into MT5's MQL5/Experts folder "
            "and attach it to a DEMO chart with AutoTrading enabled.\n"
            f"Import error: {exc}"
        ) from exc
    return mt5


class MT5Client:
    """Thin wrapper around a logged-in FXPesa MT5 terminal."""

    def __init__(self, credentials: Optional[MT5Credentials] = None) -> None:
        self.credentials = credentials or MT5Credentials.from_env()
        self._mt5 = None
        self._connected = False

    def connect(self) -> None:
        mt5 = _import_mt5()
        kwargs = {}
        if self.credentials.path:
            kwargs["path"] = self.credentials.path
        if self.credentials.login and self.credentials.password and self.credentials.server:
            kwargs.update(
                login=self.credentials.login,
                password=self.credentials.password,
                server=self.credentials.server,
            )
        if not mt5.initialize(**kwargs):
            err = mt5.last_error()
            raise MT5Unavailable(
                f"MT5 initialize() failed: {err}. Open the FXPesa MT5 terminal, "
                "log in, then retry. Server name is on the MT5 login screen."
            )
        self._mt5 = mt5
        self._connected = True
        info = mt5.account_info()
        if info is None:
            raise MT5Unavailable(f"MT5 connected but account_info() is empty: {mt5.last_error()}")
        LOGGER.info(
            "MT5 connected login=%s server=%s company=%s balance=%.2f demo=%s",
            info.login,
            info.server,
            info.company,
            info.balance,
            self.is_demo(),
        )

    def shutdown(self) -> None:
        if self._mt5 is not None and self._connected:
            self._mt5.shutdown()
        self._connected = False

    def is_demo(self) -> bool:
        mt5 = self._require()
        info = mt5.account_info()
        if info is None:
            return False
        return info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO

    def copy_rates(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        mt5 = self._require()
        tf = _mt5_timeframe(mt5, timeframe)
        if not mt5.symbol_select(symbol, True):
            raise MT5Unavailable(
                f"Symbol {symbol} is not in Market Watch. Add it in MT5, then retry."
            )
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            raise MT5Unavailable(
                f"No rates for {symbol} {timeframe}: {mt5.last_error()}"
            )
        frame = pd.DataFrame(rates)
        frame["timestamp"] = pd.to_datetime(frame["time"], unit="s")
        if "tick_volume" in frame.columns:
            frame["volume"] = frame["tick_volume"].astype(float)
        elif "real_volume" in frame.columns:
            frame["volume"] = frame["real_volume"].astype(float)
        else:
            frame["volume"] = 1.0
        return frame[["timestamp", "open", "high", "low", "close", "volume"]].copy()

    def _require(self):
        if not self._connected or self._mt5 is None:
            raise MT5Unavailable("MT5Client.connect() was not called")
        return self._mt5


def _mt5_timeframe(mt5, timeframe: str):
    key = timeframe.strip().upper()
    mapping = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    if key not in mapping:
        raise ValueError(f"Unsupported timeframe {timeframe!r}; use one of {sorted(mapping)}")
    return mapping[key]
