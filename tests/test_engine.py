from __future__ import annotations

from pilbet.config import BotConfig
from pilbet.data_pipeline import DataPipeline
from pilbet.engine import BacktestEngine
from pilbet.models import ExitReason, OrderKind, Side, Signal
from tests.helpers import ohlc_frame, paint_death_cross, paint_golden_cross, spot_config


def test_signal_fills_at_next_open_not_signal_close() -> None:
    frame = paint_golden_cross(ohlc_frame(n=56, price=100.0), at=50)
    frame.loc[50, ["open", "high", "low", "close"]] = (100.0, 101.0, 99.0, 100.0)
    # Entry is at bar 51 open. Keep later bars inside the 1.5%/3% bracket around 105.
    frame.loc[51:, ["open", "high", "low", "close"]] = (105.0, 105.5, 104.8, 105.2)

    engine = BacktestEngine(spot_config(starting_cash=10_000.0, flatten_at_end=False))
    result = engine.run(frame)
    assert result.trades == []
    assert engine.portfolio.has_position
    assert engine.portfolio.position.entry_price == 105.0
    assert engine.portfolio.position.entry_index == 51


def test_same_bar_stop_after_entry() -> None:
    frame = paint_golden_cross(ohlc_frame(n=54, price=100.0), at=50)
    frame.loc[51, ["open", "high", "low", "close"]] = (100.0, 100.4, 98.0, 98.5)
    engine = BacktestEngine(spot_config(flatten_at_end=False))
    result = engine.run(frame)
    assert len(result.trades) == 1
    assert result.trades[0].reason is ExitReason.STOP_LOSS
    assert result.trades[0].entry_index == 51
    assert result.trades[0].exit_index == 51


def test_death_cross_exits_next_open() -> None:
    frame = paint_golden_cross(ohlc_frame(n=58, price=100.0), at=50)
    # Keep the bullish SMA state until bar 52, then death-cross on 53.
    frame.loc[51:52, "sma_short"] = 101.0
    frame.loc[51:52, "sma_long"] = 100.0
    frame.loc[53:, "sma_short"] = 99.0
    frame.loc[53:, "sma_long"] = 100.0
    # Stay inside the 100-entry bracket until the reversal fill.
    frame.loc[51:54, ["open", "high", "low", "close"]] = (100.2, 100.7, 99.6, 100.1)
    frame.loc[54, ["open", "high", "low", "close"]] = (99.8, 100.0, 99.4, 99.6)

    engine = BacktestEngine(spot_config(flatten_at_end=False))
    result = engine.run(frame)
    assert len(result.trades) == 1
    assert result.trades[0].reason is ExitReason.STRATEGIC_REVERSAL
    assert result.trades[0].exit_index == 54
    assert result.trades[0].exit_price == 99.8


def test_death_cross_opens_short_when_flat() -> None:
    frame = paint_death_cross(ohlc_frame(n=56, price=100.0), at=50)
    frame.loc[51:, ["open", "high", "low", "close"]] = (100.0, 100.4, 99.6, 100.0)
    engine = BacktestEngine(spot_config(allow_short=True, flatten_at_end=False))
    engine.run(frame)
    assert engine.portfolio.has_position
    assert engine.portfolio.position.side is Side.SHORT
    assert engine.portfolio.position.entry_index == 51


def test_gap_stop_fills_open_not_stop_price() -> None:
    frame = paint_golden_cross(ohlc_frame(n=55, price=100.0), at=50)
    frame.loc[51, ["open", "high", "low", "close"]] = (100.0, 100.5, 99.4, 100.1)
    frame.loc[52, ["open", "high", "low", "close"]] = (96.0, 97.0, 95.5, 96.5)
    engine = BacktestEngine(spot_config(flatten_at_end=False))
    result = engine.run(frame)
    assert len(result.trades) == 1
    assert result.trades[0].reason is ExitReason.STOP_LOSS_GAP
    assert result.trades[0].exit_price == 96.0


def test_suppresses_entry_while_long() -> None:
    frame = paint_golden_cross(ohlc_frame(n=56, price=100.0), at=50)
    frame.loc[51, ["open", "high", "low", "close"]] = (100.0, 100.5, 99.5, 100.2)
    engine = BacktestEngine(spot_config(flatten_at_end=False))
    engine.run(frame)
    assert engine.portfolio.has_position
    engine._queue_signal(frame, 51)
    assert engine.pending is None or engine.pending.kind is not OrderKind.ENTRY_LONG
    assert engine.strategy.evaluate(frame.iloc[:52]) is Signal.HOLD


def test_synthetic_end_to_end_is_solvent() -> None:
    config = BotConfig(symbol="XAUUSD", flatten_at_end=True)
    candles = DataPipeline(config).load_synthetic(n_bars=250, seed=42, start_price=2400.0)
    result = BacktestEngine(config).run(candles)
    assert result.report.ending_equity > 0
    assert result.report.fees_paid >= 0
    cash_ok = all(point.cash >= -1e-6 for point in result.equity_curve)
    assert cash_ok
