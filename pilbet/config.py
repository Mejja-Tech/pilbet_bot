"""Runtime configuration for the SMA-crossover execution system."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pilbet.fx import infer_contract_size, infer_pip_size, instrument_defaults


@dataclass(frozen=True)
class BotConfig:
    """Knobs for an FXPesa / MT5-style book, with an optional spot-cash mode.

    Defaults match FXPesa XAUUSD: 1% equity risk, 400-pip ($4) stop, 800-pip
    target, ~30-pip spread, 1:100 leverage, lots from 0.01. Pip brackets are
    inferred from ``symbol`` when left as ``None``.
    """

    symbol: str = "XAUUSD"
    timeframe: str = "H1"
    starting_cash: float = 10_000.0
    short_window: int = 20
    long_window: int = 50
    risk_fraction: float = 0.01
    stop_loss_pips: float | None = None
    take_profit_pips: float | None = None
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    pip_size: float = 0.0
    contract_size: float = 0.0
    volume_min: float = 0.01
    volume_step: float = 0.01
    volume_max: float | None = None
    leverage: float = 100.0
    spread_pips: float | None = None
    slippage_pips: float = 0.0
    fee_rate: float = 0.0
    commission_per_lot: float = 0.0
    slippage_rate: float = 0.0
    quantity_precision: int = 2
    min_notional: float = 0.0
    allow_short: bool = True
    use_margin: bool = True
    flatten_at_end: bool = True
    drop_incomplete_last_bar: bool = True
    last_bar_closed: bool = True
    ambiguous_exit_policy: str = "stop_first"
    reports_dir: Path = field(default_factory=lambda: Path("reports"))
    log_file: Path | None = field(default_factory=lambda: Path("logs/pilbet.log"))

    def __post_init__(self) -> None:
        spec = instrument_defaults(self.symbol)
        if self.stop_loss_pips is None:
            object.__setattr__(self, "stop_loss_pips", spec["stop_loss_pips"])
        if self.take_profit_pips is None:
            object.__setattr__(self, "take_profit_pips", spec["take_profit_pips"])
        if self.spread_pips is None:
            object.__setattr__(self, "spread_pips", spec["spread_pips"])
        if self.volume_max is None:
            object.__setattr__(self, "volume_max", spec["volume_max"])

        if self.short_window < 2:
            raise ValueError("short_window must be >= 2")
        if self.long_window <= self.short_window:
            raise ValueError("long_window must be greater than short_window")
        if not 0.0 < self.risk_fraction <= 1.0:
            raise ValueError("risk_fraction must be in (0, 1]")
        if self.stop_loss_pips <= 0 and self.stop_loss_pct <= 0:
            raise ValueError("set stop_loss_pips or stop_loss_pct")
        if self.take_profit_pips <= 0 and self.take_profit_pct <= 0:
            raise ValueError("set take_profit_pips or take_profit_pct")
        if self.stop_loss_pct < 0.0 or self.take_profit_pct < 0.0:
            raise ValueError("stop_loss_pct and take_profit_pct must be >= 0")
        if 0.0 < self.stop_loss_pct >= 1.0:
            raise ValueError("stop_loss_pct must be in (0, 1) when used")
        if self.fee_rate < 0.0 or self.slippage_rate < 0.0:
            raise ValueError("fee_rate and slippage_rate must be >= 0")
        if self.spread_pips < 0.0 or self.slippage_pips < 0.0:
            raise ValueError("spread_pips and slippage_pips must be >= 0")
        if self.commission_per_lot < 0.0:
            raise ValueError("commission_per_lot must be >= 0")
        if self.ambiguous_exit_policy not in {"stop_first", "take_profit_first"}:
            raise ValueError(
                "ambiguous_exit_policy must be 'stop_first' or 'take_profit_first'"
            )
        if self.starting_cash <= 0:
            raise ValueError("starting_cash must be positive")
        if self.quantity_precision < 0:
            raise ValueError("quantity_precision must be >= 0")
        if self.leverage <= 0:
            raise ValueError("leverage must be positive")
        if self.volume_min < 0 or self.volume_step < 0 or self.volume_max < 0:
            raise ValueError("volume bounds must be >= 0")
        if self.volume_max and self.volume_min > self.volume_max:
            raise ValueError("volume_min cannot exceed volume_max")

        pip = self.pip_size if self.pip_size > 0 else infer_pip_size(self.symbol)
        contract = (
            self.contract_size
            if self.contract_size > 0
            else infer_contract_size(self.symbol)
        )
        object.__setattr__(self, "pip_size", pip)
        object.__setattr__(self, "contract_size", contract)

    def stop_distance(self, entry_price: float) -> float:
        if self.stop_loss_pips > 0:
            return self.stop_loss_pips * self.pip_size
        return entry_price * self.stop_loss_pct

    def take_profit_distance(self, entry_price: float) -> float:
        if self.take_profit_pips > 0:
            return self.take_profit_pips * self.pip_size
        return entry_price * self.take_profit_pct

    def long_brackets(self, entry_price: float) -> tuple[float, float]:
        sl = entry_price - self.stop_distance(entry_price)
        tp = entry_price + self.take_profit_distance(entry_price)
        return sl, tp

    def short_brackets(self, entry_price: float) -> tuple[float, float]:
        sl = entry_price + self.stop_distance(entry_price)
        tp = entry_price - self.take_profit_distance(entry_price)
        return sl, tp

    def pip_value_per_lot(self) -> float:
        return self.contract_size * self.pip_size

    def spread_price(self) -> float:
        return (self.spread_pips * self.pip_size) / 2.0

    def slippage_price(self) -> float:
        return self.slippage_pips * self.pip_size
