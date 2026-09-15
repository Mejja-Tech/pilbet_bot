from __future__ import annotations

from pilbet.config import BotConfig
from pilbet.models import Signal
from pilbet.strategy import SMACrossoverStrategy
from tests.helpers import ohlc_frame, paint_death_cross, paint_golden_cross


def test_hold_until_warmup() -> None:
    strategy = SMACrossoverStrategy(BotConfig())
    frame = ohlc_frame(n=40)
    assert strategy.evaluate(frame) is Signal.HOLD


def test_golden_cross_uses_completed_pair() -> None:
    strategy = SMACrossoverStrategy(BotConfig())
    frame = paint_golden_cross(ohlc_frame(n=55), at=50)
    assert strategy.evaluate(frame.iloc[:50]) is Signal.HOLD
    assert strategy.evaluate(frame.iloc[:51]) is Signal.GOLDEN_CROSS
    assert strategy.evaluate(frame.iloc[:52]) is Signal.HOLD


def test_death_cross_uses_completed_pair() -> None:
    strategy = SMACrossoverStrategy(BotConfig())
    frame = paint_death_cross(ohlc_frame(n=55), at=50)
    assert strategy.evaluate(frame.iloc[:51]) is Signal.DEATH_CROSS


def test_no_centered_window_dependency() -> None:
    """A later bar must not change the signal computed on an earlier prefix."""
    strategy = SMACrossoverStrategy(BotConfig())
    frame = paint_golden_cross(ohlc_frame(n=60), at=50)
    early = strategy.evaluate(frame.iloc[:51])
    frame.loc[59, "sma_short"] = 50.0
    frame.loc[59, "sma_long"] = 150.0
    assert strategy.evaluate(frame.iloc[:51]) is early
