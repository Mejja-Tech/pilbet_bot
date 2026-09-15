"""Pilbet: a modular SMA-crossover execution system."""

from pilbet.config import BotConfig
from pilbet.data_pipeline import DataPipeline
from pilbet.engine import BacktestEngine, BacktestResult
from pilbet.portfolio import Portfolio
from pilbet.risk_manager import RiskManager
from pilbet.strategy import SMACrossoverStrategy

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "BotConfig",
    "DataPipeline",
    "Portfolio",
    "RiskManager",
    "SMACrossoverStrategy",
]
