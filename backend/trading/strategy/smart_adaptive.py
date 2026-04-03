import statistics

from backend.trading.models import Signal
from backend.trading.strategy.base import Strategy


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _rsi(values: list[float], period: int) -> float:
    if len(values) < period + 1:
        return 50.0
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        d = values[i] - values[i - 1]
        if d > 0:
            gains += d
        else:
            losses += -d
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - (100.0 / (1.0 + rs))


class SmartAdaptiveStrategy(Strategy):
    """
    智能自适应策略（仅用收盘价，兼容现有引擎）
    - 趋势状态：EMA趋势 + RSI回调入场 + 过热/走弱离场
    - 震荡状态：Z-Score 均值回归（低吸高抛）
    """

    name = "smart_adaptive"

    def __init__(
        self,
        fast: int = 12,
        slow: int = 48,
        trend_threshold_pct: float = 0.18,
        trend_exit_threshold_pct: float = 0.05,
        rsi_period: int = 14,
        trend_pullback_rsi: float = 46.0,
        trend_takeprofit_rsi: float = 69.0,
        bb_window: int = 20,
        trend_pullback_z: float = 0.35,
        trend_takeprofit_z: float = 1.25,
        range_entry_z: float = 1.15,
        range_exit_z: float = 0.75,
        range_buy_rsi: float = 43.0,
        range_sell_rsi: float = 58.0,
        vol_window: int = 20,
        max_vol_pct: float = 2.8,
        cooldown_bars: int = 2,
    ) -> None:
        self.fast = fast
        self.slow = slow
        self.trend_threshold_pct = trend_threshold_pct
        self.trend_exit_threshold_pct = trend_exit_threshold_pct
        self.rsi_period = rsi_period
        self.trend_pullback_rsi = trend_pullback_rsi
        self.trend_takeprofit_rsi = trend_takeprofit_rsi
        self.bb_window = bb_window
        self.trend_pullback_z = trend_pullback_z
        self.trend_takeprofit_z = trend_takeprofit_z
        self.range_entry_z = range_entry_z
        self.range_exit_z = range_exit_z
        self.range_buy_rsi = range_buy_rsi
        self.range_sell_rsi = range_sell_rsi
        self.vol_window = vol_window
        self.max_vol_pct = max_vol_pct
        self.cooldown_bars = cooldown_bars

        self._last_action_len = -10_000

    def _compute_features(self, closes: list[float]) -> dict[str, float]:
        fast_ema = _ema(closes, self.fast)[-1]
        slow_ema = _ema(closes, self.slow)[-1]
        trend_pct = 0.0 if slow_ema == 0 else ((fast_ema - slow_ema) / slow_ema) * 100.0

        rsi = _rsi(closes, self.rsi_period)

        chunk = closes[-self.bb_window :]
        mean = statistics.mean(chunk)
        std = statistics.pstdev(chunk) if len(chunk) > 1 else 0.0
        price = closes[-1]
        z = 0.0 if std <= 1e-12 else (price - mean) / std

        ret_chunk = closes[-(self.vol_window + 1) :]
        rets = []
        for i in range(1, len(ret_chunk)):
            base = ret_chunk[i - 1]
            if base <= 0:
                continue
            rets.append((ret_chunk[i] - base) / base)
        vol_pct = (statistics.pstdev(rets) * 100.0) if len(rets) > 1 else 0.0

        return {
            "price": float(price),
            "fast_ema": float(fast_ema),
            "slow_ema": float(slow_ema),
            "trend_pct": float(trend_pct),
            "rsi": float(rsi),
            "mean": float(mean),
            "std": float(std),
            "z": float(z),
            "vol_pct": float(vol_pct),
        }

    def generate_signal(self, closes: list[float]) -> Signal:
        min_need = max(self.slow + 2, self.bb_window + 2, self.rsi_period + 2, self.vol_window + 2)
        if len(closes) < min_need:
            return "hold"

        if len(closes) - self._last_action_len < self.cooldown_bars:
            return "hold"

        f = self._compute_features(closes)
        trend_pct = f["trend_pct"]
        rsi = f["rsi"]
        z = f["z"]
        vol_pct = f["vol_pct"]

        is_trend = abs(trend_pct) >= self.trend_threshold_pct
        is_volatile = vol_pct >= self.max_vol_pct

        signal: Signal = "hold"

        if is_trend and not is_volatile:
            # 趋势模式：只顺势开仓，逆势优先平仓
            if trend_pct > 0:
                if rsi <= self.trend_pullback_rsi and z <= -self.trend_pullback_z:
                    signal = "buy"
                elif trend_pct <= self.trend_exit_threshold_pct or rsi >= self.trend_takeprofit_rsi or z >= self.trend_takeprofit_z:
                    signal = "sell"
            else:
                # 下行趋势不做多，优先给离场信号
                if trend_pct >= -self.trend_exit_threshold_pct or rsi <= 50.0:
                    signal = "hold"
                else:
                    signal = "sell"
        else:
            # 震荡模式：均值回归
            if z <= -self.range_entry_z and rsi <= self.range_buy_rsi and not is_volatile:
                signal = "buy"
            elif z >= self.range_exit_z or rsi >= self.range_sell_rsi:
                signal = "sell"

        if signal in {"buy", "sell"}:
            self._last_action_len = len(closes)
        return signal

    def update_params(self, params: dict[str, float | int]) -> None:
        self.fast = int(params.get("fast", self.fast))
        self.slow = int(params.get("slow", self.slow))
        self.trend_threshold_pct = float(params.get("trend_threshold_pct", self.trend_threshold_pct))
        self.trend_exit_threshold_pct = float(params.get("trend_exit_threshold_pct", self.trend_exit_threshold_pct))
        self.rsi_period = int(params.get("rsi_period", self.rsi_period))
        self.trend_pullback_rsi = float(params.get("trend_pullback_rsi", self.trend_pullback_rsi))
        self.trend_takeprofit_rsi = float(params.get("trend_takeprofit_rsi", self.trend_takeprofit_rsi))
        self.bb_window = int(params.get("bb_window", self.bb_window))
        self.trend_pullback_z = float(params.get("trend_pullback_z", self.trend_pullback_z))
        self.trend_takeprofit_z = float(params.get("trend_takeprofit_z", self.trend_takeprofit_z))
        self.range_entry_z = float(params.get("range_entry_z", self.range_entry_z))
        self.range_exit_z = float(params.get("range_exit_z", self.range_exit_z))
        self.range_buy_rsi = float(params.get("range_buy_rsi", self.range_buy_rsi))
        self.range_sell_rsi = float(params.get("range_sell_rsi", self.range_sell_rsi))
        self.vol_window = int(params.get("vol_window", self.vol_window))
        self.max_vol_pct = float(params.get("max_vol_pct", self.max_vol_pct))
        self.cooldown_bars = int(params.get("cooldown_bars", self.cooldown_bars))

        if self.fast < 2 or self.slow < 3 or self.fast >= self.slow:
            raise ValueError("smart_adaptive params invalid: require 2 <= fast < slow")
        if self.rsi_period < 2:
            raise ValueError("smart_adaptive rsi_period must be >= 2")
        if self.bb_window < 5 or self.vol_window < 5:
            raise ValueError("smart_adaptive bb_window/vol_window must be >= 5")
        if self.range_entry_z <= 0 or self.range_exit_z <= 0:
            raise ValueError("smart_adaptive range z-score thresholds must be > 0")
        if self.trend_pullback_z < 0 or self.trend_takeprofit_z <= 0:
            raise ValueError("smart_adaptive trend z-score thresholds invalid")
        if self.max_vol_pct <= 0:
            raise ValueError("smart_adaptive max_vol_pct must be > 0")
        if self.cooldown_bars < 0:
            raise ValueError("smart_adaptive cooldown_bars must be >= 0")
