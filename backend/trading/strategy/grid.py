from backend.trading.models import Signal
from backend.trading.strategy.base import Strategy


class GridStrategy(Strategy):
    name = "grid"

    def __init__(self, grid_pct: float = 0.01) -> None:
        self.grid_pct = grid_pct
        self.anchor_price: float | None = None

    def generate_signal(self, closes: list[float]) -> Signal:
        if not closes:
            return "hold"
        price = closes[-1]

        if self.anchor_price is None:
            self.anchor_price = price
            return "hold"

        upper = self.anchor_price * (1.0 + self.grid_pct)
        lower = self.anchor_price * (1.0 - self.grid_pct)

        if price <= lower:
            self.anchor_price = price
            return "buy"
        if price >= upper:
            self.anchor_price = price
            return "sell"
        return "hold"

    def update_params(self, params: dict[str, float | int]) -> None:
        grid_pct = float(params.get("grid_pct", self.grid_pct))
        if grid_pct <= 0 or grid_pct >= 0.2:
            raise ValueError("grid_pct must be in (0, 0.2)")
        self.grid_pct = grid_pct
