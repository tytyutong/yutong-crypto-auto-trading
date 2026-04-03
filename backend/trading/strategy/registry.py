from backend.trading.strategy.base import Strategy
from backend.trading.strategy.bollinger import BollingerStrategy
from backend.trading.strategy.ema_cross import EMACrossStrategy
from backend.trading.strategy.grid import GridStrategy
from backend.trading.strategy.hf_scalp import HFScalpStrategy
from backend.trading.strategy.rsi import RSIStrategy
from backend.trading.strategy.smart_adaptive import SmartAdaptiveStrategy

STRATEGY_NAMES = ["ema_cross", "bollinger", "rsi", "grid", "hf_scalp", "smart_adaptive"]


def make_strategy(name: str) -> Strategy:
    key = name.strip().lower()
    if key == "ema_cross":
        return EMACrossStrategy()
    if key == "bollinger":
        return BollingerStrategy()
    if key == "rsi":
        return RSIStrategy()
    if key == "grid":
        return GridStrategy()
    if key == "hf_scalp":
        return HFScalpStrategy()
    if key == "smart_adaptive":
        return SmartAdaptiveStrategy()
    raise ValueError(f"unknown strategy: {name}")


def list_strategies() -> list[str]:
    return STRATEGY_NAMES.copy()
