from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


Side = Literal["buy", "sell"]
Signal = Literal["buy", "sell", "hold"]
Mode = Literal["paper", "live"]


@dataclass
class OrderRequest:
    symbol: str
    side: Side
    amount: float
    reason: str


@dataclass
class ExecutedOrder:
    symbol: str
    side: Side
    amount: float
    price: float
    mode: Mode
    exchange_order_id: str
    reason: str
    ts: datetime = field(default_factory=datetime.utcnow)
