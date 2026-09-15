from __future__ import annotations

from datetime import datetime

import pytest

from pilbet.models import Position, Side
from pilbet.risk_manager import RiskManager
from tests.helpers import spot_config


def test_fixed_fractional_size_from_stop_distance() -> None:
    risk = RiskManager(spot_config(starting_cash=10_000, risk_fraction=0.01, stop_loss_pct=0.015))
    decision = risk.size_long(equity=10_000, cash=10_000, entry_price=100.0)
    assert decision.accepted
    # 1% of 10_000 = $100 risk; $1.50 risk per unit -> 66.66666666 units
    assert decision.quantity == 66.66666666
    assert decision.stop_loss == 98.5
    assert decision.take_profit == 103.0
    assert decision.constraint == "risk"


def test_cash_cap_prevents_leverage() -> None:
    cfg = spot_config(min_notional=1.0, stop_loss_pct=0.001, risk_fraction=0.01)
    risk = RiskManager(cfg)
    decision = risk.size_long(equity=10_000, cash=500.0, entry_price=100.0)
    assert decision.accepted
    assert decision.constraint == "cash"
    assert decision.notional * (1.0 + cfg.fee_rate) <= 500.0 + 1e-9


def test_rejects_when_position_open() -> None:
    risk = RiskManager()
    open_pos = Position(
        side=Side.LONG,
        quantity=1.0,
        entry_price=100.0,
        entry_time=datetime(2024, 1, 1),
        entry_index=0,
        stop_loss=98.5,
        take_profit=103.0,
        entry_fee=0.05,
    )
    assert risk.can_enter(None)
    assert not risk.can_enter(open_pos)


def test_fx_lot_size_from_pip_stop() -> None:
    from pilbet.config import BotConfig

    risk = RiskManager(BotConfig(symbol="EURUSD", starting_cash=10_000))
    decision = risk.size_long(equity=10_000, cash=10_000, entry_price=1.10000)
    assert decision.accepted
    assert decision.quantity == pytest.approx(0.20)
    assert decision.stop_loss == pytest.approx(1.095)
    assert decision.take_profit == pytest.approx(1.11)
    assert decision.constraint == "risk"


def test_fx_short_brackets() -> None:
    from pilbet.config import BotConfig

    risk = RiskManager(BotConfig(symbol="EURUSD"))
    decision = risk.size_short(equity=10_000, cash=10_000, entry_price=1.10000)
    assert decision.accepted
    assert decision.side is Side.SHORT
    assert decision.stop_loss == pytest.approx(1.105)
    assert decision.take_profit == pytest.approx(1.090)


def test_xauusd_lot_size_from_pip_stop() -> None:
    from pilbet.config import BotConfig

    cfg = BotConfig()
    assert cfg.symbol == "XAUUSD"
    assert cfg.contract_size == 100.0
    assert cfg.pip_size == 0.01
    assert cfg.stop_loss_pips == 400.0
    risk = RiskManager(cfg)
    decision = risk.size_long(equity=10_000, cash=10_000, entry_price=2400.0)
    assert decision.accepted
    # $100 risk / ($4 stop × $1 pip-value / lot) = 0.25 lots
    assert decision.quantity == pytest.approx(0.25)
    assert decision.stop_loss == pytest.approx(2396.0)
    assert decision.take_profit == pytest.approx(2408.0)
    assert decision.constraint == "risk"
