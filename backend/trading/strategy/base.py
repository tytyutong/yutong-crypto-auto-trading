from abc import ABC, abstractmethod
from typing import Any

from backend.trading.models import Signal


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def generate_signal(self, closes: list[float]) -> Signal:
        raise NotImplementedError

    def update_params(self, params: dict[str, Any]) -> None:
        _ = params
