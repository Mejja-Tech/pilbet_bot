"""Typed domain objects used across the execution stack."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class Signal(str, Enum):
    """Crossover events evaluated on two completed bars only."""

    HOLD = "hold"
    GOLDEN_CROSS = "golden_cross"
    DEATH_CROSS = "death_cross"


class OrderKind(str, Enum):
    ENTRY_LONG = "entry_long"
    ENTRY_SHORT = "entry_short"
    EXIT_REVERSAL = "exit_reversal"
    REVERSE_TO_LONG = "reverse_to_long"
    REVERSE_TO_SHORT = "reverse_to_short"


class ExitReason(str, Enum):
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS_GAP = "stop_loss_gap"
    TAKE_PROFIT_GAP = "take_profit_gap"
    STOP_LOSS_AMBIGUOUS = "stop_loss_ambiguous"
    TAKE_PROFIT_AMBIGUOUS = "take_profit_ambiguous"
    STRATEGIC_REVERSAL = "strategic_reversal"
    END_OF_DATA = "end_of_data"


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def opposite(self) -> "Side":
        return Side.SHORT if self is Side.LONG else Side.LONG

    @property
    def direction(self) -> float:
        return 1.0 if self is Side.LONG else -1.0


@dataclass(frozen=True)
class Candle:
    index: int
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    sma_short: Optional[float] = None
    sma_long: Optional[float] = None


@dataclass
class Position:
    """At most one of these exists at a time (enforced by Portfolio)."""

    side: Side
    quantity: float
    entry_price: float
    entry_time: datetime
    entry_index: int
    stop_loss: float
    take_profit: float
    entry_fee: float
    contract_size: float = 1.0

    @property
    def is_open(self) -> bool:
        return self.quantity > 0.0

    def unrealized_pnl(self, mark_price: float) -> float:
        return (
            self.quantity
            * self.contract_size
            * self.side.direction
            * (mark_price - self.entry_price)
        )


@dataclass(frozen=True)
class PendingOrder:
    kind: OrderKind
    signal: Signal
    signal_index: int
    signal_time: datetime
    sma_short: float
    sma_long: float


@dataclass(frozen=True)
class Trade:
    side: Side
    quantity: float
    entry_time: datetime
    exit_time: datetime
    entry_index: int
    exit_index: int
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    entry_fee: float
    exit_fee: float
    reason: ExitReason
    pnl: float
    contract_size: float = 1.0

    @property
    def r_multiple(self) -> float:
        risk_per_unit = abs(self.entry_price - self.stop_loss)
        if risk_per_unit <= 0:
            return 0.0
        risk = self.quantity * self.contract_size * risk_per_unit
        if risk <= 0:
            return 0.0
        return self.pnl / risk


@dataclass
class EquityPoint:
    index: int
    timestamp: datetime
    cash: float
    equity: float
    unrealized_pnl: float
    realized_pnl: float
    in_position: bool


@dataclass
class SizingDecision:
    quantity: float
    stop_loss: float
    take_profit: float
    risk_dollars: float
    notional: float
    constraint: str
    side: Side = Side.LONG
    rejected_reason: Optional[str] = None

    @property
    def accepted(self) -> bool:
        return self.quantity > 0.0 and self.rejected_reason is None
