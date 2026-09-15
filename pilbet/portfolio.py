"""Cash, position, and fill accounting for a single-symbol book."""

from __future__ import annotations

import logging
from typing import Optional

from pilbet.config import BotConfig
from pilbet.models import (
    Candle,
    ExitReason,
    Position,
    Side,
    SizingDecision,
    Trade,
)

LOGGER = logging.getLogger("pilbet.portfolio")


class Portfolio:
    """Tracks cash/balance, one optional long or short, and fills.

    Intra-bar exits use the candle's high/low. If both SL and TP sit inside
    the same bar, the default policy is stop-first (conservative). A gap
    through a level fills at the open, not at the stop/target price.

    ``use_margin=True`` (FXPesa/MT5): balance is not spent on notional; equity
    is balance plus floating PnL. ``use_margin=False`` is the old spot-cash
    book used by unit tests.
    """

    def __init__(self, config: Optional[BotConfig] = None) -> None:
        self.config = config or BotConfig()
        self.starting_cash = self.config.starting_cash
        self.cash = self.config.starting_cash
        self.realized_pnl = 0.0
        self.position: Optional[Position] = None
        self.trades: list[Trade] = []
        self.fees_paid = 0.0
        self.ambiguous_exits = 0

    @property
    def has_position(self) -> bool:
        return self.position is not None and self.position.is_open

    def mark_equity(self, mark_price: float) -> float:
        if self.config.use_margin:
            return self.cash + self.unrealized_pnl(mark_price)
        return self.cash + self._position_value(mark_price)

    def unrealized_pnl(self, mark_price: float) -> float:
        if self.position is None:
            return 0.0
        return self.position.unrealized_pnl(mark_price)

    def open_long(
        self,
        *,
        candle: Candle,
        fill_price: float,
        sizing: SizingDecision,
    ) -> Position:
        return self.open_position(
            side=Side.LONG, candle=candle, fill_price=fill_price, sizing=sizing
        )

    def open_short(
        self,
        *,
        candle: Candle,
        fill_price: float,
        sizing: SizingDecision,
    ) -> Position:
        return self.open_position(
            side=Side.SHORT, candle=candle, fill_price=fill_price, sizing=sizing
        )

    def open_position(
        self,
        *,
        side: Side,
        candle: Candle,
        fill_price: float,
        sizing: SizingDecision,
    ) -> Position:
        if self.has_position:
            raise RuntimeError("Cannot open a second position (one-trade invariant)")
        if not sizing.accepted:
            raise ValueError("Refusing to open with a rejected sizing decision")

        fee = self._entry_fee(sizing.quantity, fill_price)
        if self.config.use_margin:
            if fee > self.cash + 1e-9:
                raise RuntimeError("Insufficient cash for entry fees")
            self.cash -= fee
        else:
            notional = sizing.quantity * fill_price
            cost = notional + fee
            if cost > self.cash + 1e-9:
                raise RuntimeError("Insufficient cash for entry")
            self.cash -= cost

        self.fees_paid += fee
        self.position = Position(
            side=side,
            quantity=sizing.quantity,
            entry_price=fill_price,
            entry_time=candle.timestamp,
            entry_index=candle.index,
            stop_loss=sizing.stop_loss,
            take_profit=sizing.take_profit,
            entry_fee=fee,
            contract_size=self._unit_multiplier(),
        )
        LOGGER.info(
            "ORDER ENTRY %s price=%.5f qty=%.8f sl=%.5f tp=%.5f fee=%.4f cash=%.2f",
            side.value.upper(),
            fill_price,
            sizing.quantity,
            sizing.stop_loss,
            sizing.take_profit,
            fee,
            self.cash,
        )
        return self.position

    def close_position(
        self,
        *,
        candle: Candle,
        fill_price: float,
        reason: ExitReason,
    ) -> Trade:
        if self.position is None:
            raise RuntimeError("No open position to close")

        pos = self.position
        fee = self._exit_fee(pos.quantity, fill_price)
        gross = (
            pos.quantity
            * pos.contract_size
            * pos.side.direction
            * (fill_price - pos.entry_price)
        )

        if self.config.use_margin:
            self.cash += gross - fee
        else:
            proceeds = pos.quantity * fill_price
            self.cash += proceeds - fee
            gross = proceeds - (pos.quantity * pos.entry_price)

        self.fees_paid += fee
        pnl = gross - fee - pos.entry_fee
        self.realized_pnl += pnl
        trade = Trade(
            side=pos.side,
            quantity=pos.quantity,
            entry_time=pos.entry_time,
            exit_time=candle.timestamp,
            entry_index=pos.entry_index,
            exit_index=candle.index,
            entry_price=pos.entry_price,
            exit_price=fill_price,
            stop_loss=pos.stop_loss,
            take_profit=pos.take_profit,
            entry_fee=pos.entry_fee,
            exit_fee=fee,
            reason=reason,
            pnl=pnl,
            contract_size=pos.contract_size,
        )
        self.trades.append(trade)
        self.position = None
        LOGGER.info(
            "EXIT %s %s price=%.5f qty=%.8f pnl=%.2f fee=%.4f realized=%.2f cash=%.2f",
            pos.side.value,
            reason.value,
            fill_price,
            pos.quantity,
            pnl,
            fee,
            self.realized_pnl,
            self.cash,
        )
        return trade

    def resolve_gap_exit(self, candle: Candle) -> Optional[tuple[float, ExitReason]]:
        """Gaps are the only known path at the open print."""
        if self.position is None:
            return None
        pos = self.position
        if pos.side is Side.LONG:
            if candle.open <= pos.stop_loss:
                return candle.open, ExitReason.STOP_LOSS_GAP
            if candle.open >= pos.take_profit:
                return candle.open, ExitReason.TAKE_PROFIT_GAP
            return None
        if candle.open >= pos.stop_loss:
            return candle.open, ExitReason.STOP_LOSS_GAP
        if candle.open <= pos.take_profit:
            return candle.open, ExitReason.TAKE_PROFIT_GAP
        return None

    def resolve_intrabar_exit(self, candle: Candle) -> Optional[tuple[float, ExitReason]]:
        """Use high/low after the open. Both-in-range bars default to stop-first."""
        if self.position is None:
            return None
        pos = self.position
        if pos.side is Side.LONG:
            hit_sl = candle.low <= pos.stop_loss
            hit_tp = candle.high >= pos.take_profit
        else:
            hit_sl = candle.high >= pos.stop_loss
            hit_tp = candle.low <= pos.take_profit

        if hit_sl and hit_tp:
            self.ambiguous_exits += 1
            if self.config.ambiguous_exit_policy == "take_profit_first":
                return pos.take_profit, ExitReason.TAKE_PROFIT_AMBIGUOUS
            return pos.stop_loss, ExitReason.STOP_LOSS_AMBIGUOUS
        if hit_sl:
            return pos.stop_loss, ExitReason.STOP_LOSS
        if hit_tp:
            return pos.take_profit, ExitReason.TAKE_PROFIT
        return None

    def apply_slippage(self, price: float, *, is_buy: bool) -> float:
        return self.apply_fill_price(price, is_buy=is_buy)

    def apply_fill_price(self, price: float, *, is_buy: bool) -> float:
        adj = self.config.spread_price() + self.config.slippage_price()
        if self.config.slippage_rate > 0:
            pct = price * self.config.slippage_rate
            adj += pct
        if is_buy:
            return price + adj
        return price - adj

    def _unit_multiplier(self) -> float:
        if self.config.use_margin:
            return self.config.contract_size
        return 1.0

    def _entry_fee(self, quantity: float, fill_price: float) -> float:
        cfg = self.config
        notion = quantity * fill_price * (cfg.contract_size if cfg.use_margin else 1.0)
        return quantity * cfg.commission_per_lot + notion * cfg.fee_rate

    def _exit_fee(self, quantity: float, fill_price: float) -> float:
        return self._entry_fee(quantity, fill_price)

    def _position_value(self, mark_price: float) -> float:
        if self.position is None:
            return 0.0
        return self.position.quantity * mark_price
