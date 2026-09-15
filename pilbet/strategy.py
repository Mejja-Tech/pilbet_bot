"""Moving-average crossover signals on completed candles only."""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from pilbet.config import BotConfig
from pilbet.models import Signal

LOGGER = logging.getLogger("pilbet.strategy")


class SMACrossoverStrategy:
    """Golden/death cross from ``iloc[-2]`` vs ``iloc[-1]``.

    The caller must pass a history slice that *ends on a completed bar*.
    Using the still-forming last bar would repaint the SMA as ticks arrive.
    """

    def __init__(self, config: Optional[BotConfig] = None) -> None:
        self.config = config or BotConfig()

    def evaluate(self, history: pd.DataFrame) -> Signal:
        if len(history) < self.config.long_window + 1:
            return Signal.HOLD

        prev = history.iloc[-2]
        last = history.iloc[-1]
        needed = ("sma_short", "sma_long")
        if any(pd.isna(prev[col]) or pd.isna(last[col]) for col in needed):
            return Signal.HOLD

        prev_bull = float(prev["sma_short"]) > float(prev["sma_long"])
        last_bull = float(last["sma_short"]) > float(last["sma_long"])

        if (not prev_bull) and last_bull:
            LOGGER.info(
                "Crossover detected GOLDEN_CROSS ts=%s sma_short=%.4f sma_long=%.4f "
                "(evaluated on completed bars iloc[-2] vs iloc[-1])",
                last["timestamp"],
                float(last["sma_short"]),
                float(last["sma_long"]),
            )
            return Signal.GOLDEN_CROSS

        if prev_bull and (not last_bull):
            LOGGER.info(
                "Crossover detected DEATH_CROSS ts=%s sma_short=%.4f sma_long=%.4f "
                "(evaluated on completed bars iloc[-2] vs iloc[-1])",
                last["timestamp"],
                float(last["sma_short"]),
                float(last["sma_long"]),
            )
            return Signal.DEATH_CROSS

        return Signal.HOLD
