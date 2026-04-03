from backend.trading.models import Signal
from backend.trading.strategy.base import Strategy


class HFScalpStrategy(Strategy):
    """
    高频短线动量策略（适合 1s/1m）
    - 当短窗口涨幅超过 entry_bps 时给 buy
    - 当短窗口跌幅超过 exit_bps 时给 sell
    """

    name = "hf_scalp"

    def __init__(self, lookback: int = 4, entry_bps: float = 3.0, exit_bps: float = 2.0) -> None:
        self.lookback = lookback
        self.entry_bps = entry_bps
        self.exit_bps = exit_bps

    def generate_signal(self, closes: list[float]) -> Signal:
        if len(closes) < self.lookback + 2:
            return "hold"

        current = float(closes[-1])
        base = float(closes[-1 - self.lookback])
        if base <= 0:
            return "hold"

        ret = (current - base) / base
        entry = self.entry_bps / 10000.0
        exit_ = self.exit_bps / 10000.0

        if ret >= entry:
            return "buy"
        if ret <= -exit_:
            return "sell"
        return "hold"

    def update_params(self, params: dict[str, float | int]) -> None:
        lookback = int(params.get("lookback", self.lookback))
        entry_bps = float(params.get("entry_bps", self.entry_bps))
        exit_bps = float(params.get("exit_bps", self.exit_bps))

        if lookback < 1:
            raise ValueError("hf_scalp lookback must be >= 1")
        if entry_bps <= 0:
            raise ValueError("hf_scalp entry_bps must be > 0")
        if exit_bps <= 0:
            raise ValueError("hf_scalp exit_bps must be > 0")

        self.lookback = lookback
        self.entry_bps = entry_bps
        self.exit_bps = exit_bps
