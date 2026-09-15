"""FX / MT5 symbol helpers (pips, contract size, lot snapping)."""

from __future__ import annotations

import math
import re


def normalize_symbol(symbol: str) -> str:
    """Strip broker suffixes like ``EURUSDm`` or ``EURUSD.``. """
    raw = symbol.strip().upper()
    return re.sub(r"[^A-Z0-9]", "", raw)


def infer_pip_size(symbol: str) -> float:
    """Point size of *one pip* for common FXPesa symbols."""
    name = normalize_symbol(symbol)
    if "JPY" in name or name.startswith("XAU") or name.startswith("XAG"):
        return 0.01
    return 0.0001


def infer_contract_size(symbol: str) -> float:
    name = normalize_symbol(symbol)
    if name.startswith("XAU"):
        return 100.0
    if name.startswith("XAG"):
        return 5000.0
    return 100_000.0


def instrument_defaults(symbol: str) -> dict[str, float]:
    """Pip stops/spread that match typical FXPesa H1 ranges — not EURUSD on gold."""
    name = normalize_symbol(symbol)
    if name.startswith("XAU"):
        return {
            "stop_loss_pips": 400.0,
            "take_profit_pips": 800.0,
            "spread_pips": 30.0,
            "volume_max": 1.0,
        }
    if name.startswith("XAG"):
        return {
            "stop_loss_pips": 200.0,
            "take_profit_pips": 400.0,
            "spread_pips": 25.0,
            "volume_max": 1.0,
        }
    return {
        "stop_loss_pips": 50.0,
        "take_profit_pips": 100.0,
        "spread_pips": 1.2,
        "volume_max": 2.0,
    }


def infer_synthetic_price(symbol: str) -> float:
    name = normalize_symbol(symbol)
    if name.startswith("XAU"):
        return 2400.0
    if name.startswith("XAG"):
        return 30.0
    return 1.10


def snap_volume(quantity: float, step: float, precision: int) -> float:
    """Round down to the broker volume step, then to ``precision`` decimals."""
    if quantity <= 0:
        return 0.0
    if step > 0:
        quantity = math.floor(quantity / step + 1e-12) * step
    if precision <= 0:
        return float(math.floor(quantity + 1e-12))
    scale = 10**precision
    return math.floor(quantity * scale + 1e-12) / scale
