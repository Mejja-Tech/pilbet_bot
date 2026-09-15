"""Fixed-fractional sizing and hard SL/TP brackets."""

from __future__ import annotations

import logging
from typing import Optional

from pilbet.config import BotConfig
from pilbet.fx import snap_volume
from pilbet.models import Position, Side, SizingDecision

LOGGER = logging.getLogger("pilbet.risk")


class RiskManager:
    """Van Tharp-style size: dollars risked / dollars risked per unit.

    On FXPesa/MT5 this is lot-based: ``lots = risk_dollars / (stop_pips *
    pip_value)``, then snapped to ``volume_step`` and capped by free margin
    and ``volume_max``. Spot-cash mode (``use_margin=False``) still spends
    notional from cash so a tight stop cannot imply leverage.
    """

    def __init__(self, config: Optional[BotConfig] = None) -> None:
        self.config = config or BotConfig()

    def can_enter(self, position: Optional[Position]) -> bool:
        """Invariant: at most one open position."""
        return position is None or not position.is_open

    def size_long(
        self,
        *,
        equity: float,
        cash: float,
        entry_price: float,
    ) -> SizingDecision:
        return self.size(
            side=Side.LONG, equity=equity, cash=cash, entry_price=entry_price
        )

    def size_short(
        self,
        *,
        equity: float,
        cash: float,
        entry_price: float,
    ) -> SizingDecision:
        return self.size(
            side=Side.SHORT, equity=equity, cash=cash, entry_price=entry_price
        )

    def size(
        self,
        *,
        side: Side,
        equity: float,
        cash: float,
        entry_price: float,
    ) -> SizingDecision:
        cfg = self.config
        if side is Side.LONG:
            stop_loss, take_profit = cfg.long_brackets(entry_price)
        else:
            stop_loss, take_profit = cfg.short_brackets(entry_price)

        risk_per_unit = abs(entry_price - stop_loss)
        risk_dollars = max(equity, 0.0) * cfg.risk_fraction

        if entry_price <= 0 or risk_per_unit <= 0 or risk_dollars <= 0:
            return self._reject(
                side, "invalid_price_or_equity", stop_loss, take_profit, 0.0
            )

        if cfg.use_margin:
            return self._size_margin(
                side=side,
                equity=equity,
                cash=cash,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_per_unit=risk_per_unit,
                risk_dollars=risk_dollars,
            )
        return self._size_cash(
            side=side,
            cash=cash,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_per_unit=risk_per_unit,
            risk_dollars=risk_dollars,
        )

    def _size_margin(
        self,
        *,
        side: Side,
        equity: float,
        cash: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        risk_per_unit: float,
        risk_dollars: float,
    ) -> SizingDecision:
        cfg = self.config
        risk_per_lot = risk_per_unit * cfg.contract_size
        if risk_per_lot <= 0:
            return self._reject(
                side, "invalid_price_or_equity", stop_loss, take_profit, risk_dollars
            )

        raw_qty = risk_dollars / risk_per_lot
        constraint = "risk"
        qty = raw_qty

        max_by_volume = cfg.volume_max if cfg.volume_max > 0 else raw_qty
        if qty > max_by_volume:
            qty = max_by_volume
            constraint = "volume_max"

        free_margin = max(min(equity, cash) if cash > 0 else equity, 0.0)
        max_by_margin = (
            free_margin * cfg.leverage / (cfg.contract_size * entry_price)
            if cfg.contract_size * entry_price > 0
            else 0.0
        )
        if qty > max_by_margin:
            qty = max_by_margin
            constraint = "margin"

        qty = snap_volume(qty, cfg.volume_step, cfg.quantity_precision)
        if qty < cfg.volume_min or qty <= 0:
            return self._reject(
                side, "quantity_too_small", stop_loss, take_profit, risk_dollars
            )

        notional = qty * cfg.contract_size * entry_price
        LOGGER.info(
            "Sized %s lots=%.2f entry=%.5f sl=%.5f tp=%.5f risk_dollars=%.2f "
            "notional=%.2f constraint=%s",
            side.value.upper(),
            qty,
            entry_price,
            stop_loss,
            take_profit,
            risk_dollars,
            notional,
            constraint,
        )
        return SizingDecision(
            quantity=qty,
            side=side,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_dollars=risk_dollars,
            notional=notional,
            constraint=constraint,
        )

    def _size_cash(
        self,
        *,
        side: Side,
        cash: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        risk_per_unit: float,
        risk_dollars: float,
    ) -> SizingDecision:
        cfg = self.config
        if cash <= 0:
            return self._reject(
                side, "invalid_price_or_equity", stop_loss, take_profit, risk_dollars
            )

        raw_qty = risk_dollars / risk_per_unit
        max_affordable = cash / (entry_price * (1.0 + cfg.fee_rate))
        constraint = "risk"
        qty = raw_qty
        if qty > max_affordable:
            qty = max_affordable
            constraint = "cash"

        qty = snap_volume(qty, cfg.volume_step, cfg.quantity_precision)
        notional = qty * entry_price
        if qty <= 0 or (cfg.min_notional > 0 and notional < cfg.min_notional):
            return self._reject(
                side, "quantity_too_small", stop_loss, take_profit, risk_dollars
            )

        cost = notional * (1.0 + cfg.fee_rate)
        if cost > cash + 1e-9:
            return self._reject(
                side, "insufficient_cash", stop_loss, take_profit, risk_dollars
            )

        LOGGER.info(
            "Sized %s qty=%.8f entry=%.4f sl=%.4f tp=%.4f risk_dollars=%.2f "
            "notional=%.2f constraint=%s",
            side.value.upper(),
            qty,
            entry_price,
            stop_loss,
            take_profit,
            risk_dollars,
            notional,
            constraint,
        )
        return SizingDecision(
            quantity=qty,
            side=side,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_dollars=risk_dollars,
            notional=notional,
            constraint=constraint,
        )

    def _reject(
        self,
        side: Side,
        reason: str,
        stop_loss: float,
        take_profit: float,
        risk_dollars: float,
    ) -> SizingDecision:
        LOGGER.warning("Entry rejected reason=%s", reason)
        return SizingDecision(
            quantity=0.0,
            side=side,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_dollars=risk_dollars,
            notional=0.0,
            constraint="rejected",
            rejected_reason=reason,
        )
