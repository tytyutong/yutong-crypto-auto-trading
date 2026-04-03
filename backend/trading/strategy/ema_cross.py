from backend.trading.models import Signal
from backend.trading.strategy.base import Strategy


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    ema_values: list[float] = [values[0]]
    for price in values[1:]:
        ema_values.append(price * k + ema_values[-1] * (1 - k))
    return ema_values


class EMACrossStrategy(Strategy):
    name = "ema_cross"

    def __init__(self, fast: int = 9, slow: int = 21) -> None:
        if fast >= slow:
            raise ValueError("fast period must be smaller than slow period")
        self.fast = fast
        self.slow = slow

    def generate_signal(self, closes: list[float]) -> Signal:
        if len(closes) < self.slow + 2:
            return "hold"

        fast_ema = _ema(closes, self.fast)
        slow_ema = _ema(closes, self.slow)

        prev_fast, curr_fast = fast_ema[-2], fast_ema[-1]
        prev_slow, curr_slow = slow_ema[-2], slow_ema[-1]

        if prev_fast <= prev_slow and curr_fast > curr_slow:
            return "buy"
        if prev_fast >= prev_slow and curr_fast < curr_slow:
            return "sell"
        return "hold"

    def update_params(self, params: dict[str, float | int]) -> None:
        fast = int(params.get("fast", self.fast))
        slow = int(params.get("slow", self.slow))
        if fast >= slow:
            raise ValueError("EMA params invalid: fast must be smaller than slow")
        self.fast = fast
        self.slow = slow
