from backend.trading.models import Signal
from backend.trading.strategy.base import Strategy


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        delta = values[i] - values[i - 1]
        if delta > 0:
            gains += delta
        else:
            losses += -delta
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - (100.0 / (1.0 + rs))


class RSIStrategy(Strategy):
    name = "rsi"

    def __init__(self, period: int = 14, overbought: float = 70.0, oversold: float = 30.0) -> None:
        self.period = period
        self.overbought = overbought
        self.oversold = oversold

    def generate_signal(self, closes: list[float]) -> Signal:
        rsi = _rsi(closes, self.period)
        if rsi < self.oversold:
            return "buy"
        if rsi > self.overbought:
            return "sell"
        return "hold"

    def update_params(self, params: dict[str, float | int]) -> None:
        period = int(params.get("period", self.period))
        overbought = float(params.get("overbought", self.overbought))
        oversold = float(params.get("oversold", self.oversold))
        if period < 2:
            raise ValueError("rsi period must be >= 2")
        if oversold >= overbought:
            raise ValueError("rsi oversold must be < overbought")
        self.period = period
        self.overbought = overbought
        self.oversold = oversold
