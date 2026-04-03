from datetime import datetime, timezone

from backend.config import settings
from backend.trading.models import OrderRequest
from backend.trading.storage import Storage


class RiskManager:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def allow_order(
        self,
        req: OrderRequest,
        latest_price: float,
        *,
        max_notional_usdt: float | None = None,
        mode: str | None = None,
    ) -> tuple[bool, str]:
        notional = req.amount * latest_price
        limit = max_notional_usdt if max_notional_usdt is not None else settings.risk_max_position_usdt
        if notional > limit:
            return False, f"order notional {notional:.2f} > max {limit:.2f}"

        daily_pnl = self._estimate_today_pnl(req.symbol, mode=mode)
        if daily_pnl < -abs(settings.risk_max_daily_loss_usdt):
            return False, f"daily loss limit reached: {daily_pnl:.2f}"

        return True, "ok"

    def _estimate_today_pnl(self, symbol: str, mode: str | None = None) -> float:
        rows = self.storage.recent_orders(limit=500, mode=mode)
        today = datetime.now(timezone.utc).date()
        position_qty = 0.0
        avg_entry = 0.0
        realized_pnl = 0.0
        for r in rows:
            if r["symbol"] != symbol:
                continue
            ts = datetime.fromisoformat(r["ts"]).date()
            if ts != today:
                continue
            amount = float(r["amount"])
            price = float(r["price"])
            side = r["side"]

            if side == "buy":
                new_qty = position_qty + amount
                if new_qty > 0:
                    avg_entry = ((position_qty * avg_entry) + (amount * price)) / new_qty
                position_qty = new_qty
            else:
                close_qty = min(position_qty, amount)
                if close_qty > 0:
                    realized_pnl += (price - avg_entry) * close_qty
                position_qty = max(0.0, position_qty - amount)
                if position_qty <= 1e-9:
                    position_qty = 0.0
                    avg_entry = 0.0
        return realized_pnl
