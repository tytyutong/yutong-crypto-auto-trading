import statistics

from backend.trading.models import Signal
from backend.trading.strategy.base import Strategy


class BollingerStrategy(Strategy):
    name = "bollinger"

    def __init__(self, window: int = 20, std_mult: float = 2.0) -> None:
        self.window = window
        self.std_mult = std_mult

    def generate_signal(self, closes: list[float]) -> Signal:
        if len(closes) < self.window + 1:
            return "hold"

        chunk = closes[-self.window :]
        mean = statistics.mean(chunk)
        std = statistics.pstdev(chunk) if len(chunk) > 1 else 0.0
        upper = mean + self.std_mult * std
        lower = mean - self.std_mult * std
        price = closes[-1]

        if price < lower:
            return "buy"
        if price > upper:
            return "sell"
        return "hold"

    def update_params(self, params: dict[str, float | int]) -> None:
        window = int(params.get("window", self.window))
        std_mult = float(params.get("std_mult", self.std_mult))
        if window < 5:
            raise ValueError("bollinger window must be >= 5")
        if std_mult <= 0:
            raise ValueError("bollinger std_mult must be > 0")
        self.window = window
        self.std_mult = std_mult
