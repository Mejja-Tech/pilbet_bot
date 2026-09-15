from __future__ import annotations

from datetime import datetime

import pytest

from pilbet.models import Candle, ExitReason, Position, Side
from pilbet.portfolio import Portfolio
from tests.helpers import force_long, force_short, spot_config


def _candle(**kwargs) -> Candle:
    base = dict(
        index=10,
        timestamp=datetime(2024, 3, 1, 9, 30),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1_000.0,
    )
    base.update(kwargs)
    return Candle(**base)


def test_stop_loss_uses_low() -> None:
    book = Portfolio(spot_config())
    force_long(book, _candle(), quantity=10.0, entry_price=100.0)
    resolved = book.resolve_intrabar_exit(
        _candle(open=100.0, high=100.8, low=98.0, close=99.0)
    )
    assert resolved is not None
    price, reason = resolved
    assert reason is ExitReason.STOP_LOSS
    assert price == 98.5


def test_take_profit_uses_high() -> None:
    book = Portfolio(spot_config())
    force_long(book, _candle(), quantity=10.0, entry_price=100.0)
    resolved = book.resolve_intrabar_exit(
        _candle(open=100.0, high=104.0, low=99.5, close=103.5)
    )
    assert resolved is not None
    price, reason = resolved
    assert reason is ExitReason.TAKE_PROFIT
    assert price == 103.0


def test_ambiguous_bar_defaults_to_stop() -> None:
    book = Portfolio(spot_config(ambiguous_exit_policy="stop_first"))
    force_long(book, _candle(), quantity=10.0, entry_price=100.0)
    resolved = book.resolve_intrabar_exit(
        _candle(open=100.0, high=104.0, low=98.0, close=101.0)
    )
    assert resolved is not None
    price, reason = resolved
    assert reason is ExitReason.STOP_LOSS_AMBIGUOUS
    assert price == 98.5
    assert book.ambiguous_exits == 1


def test_gap_through_stop_fills_at_open() -> None:
    book = Portfolio(spot_config())
    force_long(book, _candle(), quantity=10.0, entry_price=100.0)
    resolved = book.resolve_gap_exit(
        _candle(open=97.0, high=98.0, low=96.5, close=97.5)
    )
    assert resolved is not None
    price, reason = resolved
    assert reason is ExitReason.STOP_LOSS_GAP
    assert price == 97.0


def test_short_stop_uses_high() -> None:
    book = Portfolio(spot_config(allow_short=True))
    force_short(book, _candle(), quantity=10.0, entry_price=100.0)
    resolved = book.resolve_intrabar_exit(
        _candle(open=100.0, high=102.0, low=99.5, close=101.0)
    )
    assert resolved is not None
    price, reason = resolved
    assert reason is ExitReason.STOP_LOSS
    assert price == 101.5


def test_fees_hit_cash_on_round_trip() -> None:
    book = Portfolio(spot_config(fee_rate=0.0005, starting_cash=10_000.0))
    entry = _candle(open=100.0)
    force_long(book, entry, quantity=10.0, entry_price=100.0)
    assert book.cash == pytest.approx(10_000.0 - 1_000.0 - 0.5)
    exit_bar = _candle(index=11, open=110.0, high=111.0, low=109.0, close=110.0)
    trade = book.close_position(
        candle=exit_bar, fill_price=110.0, reason=ExitReason.TAKE_PROFIT
    )
    assert trade.pnl == pytest.approx(1_100.0 - 0.55 - 1_000.0 - 0.5)
    assert book.cash == pytest.approx(10_000.0 + trade.pnl)


def test_one_position_invariant() -> None:
    book = Portfolio(spot_config())
    force_long(book, _candle(), quantity=5.0, entry_price=100.0)
    with pytest.raises(RuntimeError, match="second position"):
        force_long(book, _candle(index=11), quantity=5.0, entry_price=101.0)


def test_unrealized_tracks_mark() -> None:
    book = Portfolio(spot_config())
    force_long(book, _candle(), quantity=10.0, entry_price=100.0)
    assert book.unrealized_pnl(102.0) == pytest.approx(20.0)
    assert isinstance(book.position, Position)
    assert book.position.side is Side.LONG


def test_fx_margin_does_not_spend_notional() -> None:
    from pilbet.config import BotConfig
    from pilbet.models import SizingDecision

    cfg = BotConfig()
    book = Portfolio(cfg)
    candle = Candle(
        index=0,
        timestamp=datetime(2024, 1, 2, 0, 0),
        open=2400.0,
        high=2401.0,
        low=2399.0,
        close=2400.0,
        volume=100.0,
    )
    sizing = SizingDecision(
        quantity=0.25,
        side=Side.LONG,
        stop_loss=2396.0,
        take_profit=2408.0,
        risk_dollars=100.0,
        notional=0.25 * 100.0 * 2400.0,
        constraint="test",
    )
    book.open_long(candle=candle, fill_price=2400.0, sizing=sizing)
    assert book.cash == pytest.approx(10_000.0)
    assert book.unrealized_pnl(2401.0) == pytest.approx(0.25 * 100.0 * 1.0)
    assert book.mark_equity(2401.0) == pytest.approx(10_000.0 + 25.0)
