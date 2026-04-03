from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from backend.config import settings
from backend.trading.exchange.binance_client import BinanceClient
from backend.trading.models import OrderRequest, Signal
from backend.trading.notifier import FeishuNotifier
from backend.trading.risk import RiskManager
from backend.trading.storage import Storage
from backend.trading.strategy.base import Strategy
from backend.trading.strategy.registry import list_strategies, make_strategy


@dataclass
class OrderSizingConfig:
    amount_mode: str = "fixed"  # fixed/dynamic
    fixed_order_usdt: float = 100.0
    max_order_usdt: float = 300.0
    risk_per_trade_pct: float = 0.5
    stop_loss_pct: float = 2.0
    tradable_balance_ratio: float = 0.95
    max_open_trades: int = 5
    max_symbol_exposure_usdt: float = 800.0
    initial_entry_on_start: bool = False


@dataclass
class BotState:
    bot_id: str
    running: bool
    symbol: str
    timeframe: str
    signal_kline_limit: int
    mode: str
    strategy_name: str
    strategy_params: dict[str, Any] = field(default_factory=dict)
    order_sizing: OrderSizingConfig = field(default_factory=OrderSizingConfig)
    last_signal: Signal = "hold"
    signal_reason: str = ""
    signal_metrics: dict[str, Any] = field(default_factory=dict)
    initial_entry_done: bool = False
    last_price: float = 0.0
    last_error: str = ""
    last_tick: str = ""
    position_qty: float = 0.0
    avg_entry_price: float = 0.0
    trades_count: int = 0
    buy_count: int = 0
    sell_count: int = 0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0
    history: list[dict[str, Any]] = field(default_factory=list)


class BotRuntime:
    def __init__(
        self,
        *,
        bot_id: str,
        symbol: str,
        timeframe: str,
        signal_kline_limit: int,
        mode: str,
        strategy: Strategy,
        strategy_params: dict[str, Any],
        order_sizing: OrderSizingConfig,
        client: BinanceClient,
        storage: Storage,
        risk: RiskManager,
        notifier: FeishuNotifier,
        active_bot_counter: callable,
    ) -> None:
        self.bot_id = bot_id
        self.client = client
        self.storage = storage
        self.risk = risk
        self.notifier = notifier
        self.strategy = strategy
        self._active_bot_counter = active_bot_counter
        self.state = BotState(
            bot_id=bot_id,
            running=False,
            symbol=symbol,
            timeframe=timeframe,
            signal_kline_limit=signal_kline_limit,
            mode=mode,
            strategy_name=strategy.name,
            strategy_params=strategy_params,
            order_sizing=order_sizing,
        )
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self.state.running:
            return
        self._stop_event.clear()
        self.state.running = True
        self.notifier.send(
            "交易机器人启动",
            f"bot={self.bot_id}, symbol={self.state.symbol}, strategy={self.state.strategy_name}, mode={self.state.mode}",
        )
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        if self.state.order_sizing.initial_entry_on_start and self.state.position_qty <= 0:
            try:
                self._bootstrap_market_entry()
            except Exception as e:  # noqa: BLE001
                self.state.last_error = str(e)
                self.notifier.send("首单开仓失败", f"bot={self.bot_id}, err={e}")

    def stop(self) -> None:
        self._stop_event.set()
        self.state.running = False
        self.notifier.send(
            "交易机器人停止",
            f"bot={self.bot_id}, symbol={self.state.symbol}, strategy={self.state.strategy_name}, mode={self.state.mode}",
        )

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001
                self.state.last_error = str(e)
                self.notifier.send("交易机器人异常", f"bot={self.bot_id}, err={e}")
            time.sleep(max(settings.poll_seconds, 5))

    def _tick(self) -> None:
        ohlcv = self.client.fetch_ohlcv(
            self.state.symbol,
            self.state.timeframe,
            limit=max(50, min(1000, int(self.state.signal_kline_limit))),
        )
        closes = [row[4] for row in ohlcv]
        if not closes:
            return

        signal = self.strategy.generate_signal(closes)
        latest_price = float(closes[-1])
        reason, metrics = self._analyze_signal(closes, signal)
        self.state.last_signal = signal
        self.state.signal_reason = reason
        self.state.signal_metrics = metrics
        self.state.last_price = latest_price
        self.state.last_tick = datetime.utcnow().isoformat()

        if signal == "buy" and self.state.position_qty <= 0:
            self._place(signal, latest_price)
        elif signal == "sell" and self.state.position_qty > 0:
            self._place(signal, latest_price)

        self._update_unrealized(latest_price)

    def _analyze_signal(self, closes: list[float], signal: Signal) -> tuple[str, dict[str, Any]]:
        name = self.state.strategy_name
        metrics: dict[str, Any] = {}
        if not closes:
            return "no close data", metrics

        if name == "bollinger":
            import statistics

            window = int(getattr(self.strategy, "window", 20))
            std_mult = float(getattr(self.strategy, "std_mult", 2.0))
            if len(closes) < window + 1:
                return f"waiting candles: need >= {window + 1}, got {len(closes)}", metrics
            chunk = closes[-window:]
            mean = statistics.mean(chunk)
            std = statistics.pstdev(chunk) if len(chunk) > 1 else 0.0
            upper = mean + std_mult * std
            lower = mean - std_mult * std
            price = closes[-1]
            metrics = {
                "price": round(price, 6),
                "middle": round(mean, 6),
                "upper": round(upper, 6),
                "lower": round(lower, 6),
                "window": window,
                "std_mult": std_mult,
            }
            if signal == "buy":
                return "price broke below lower band", metrics
            if signal == "sell":
                return "price broke above upper band", metrics
            return "price is between lower and upper band", metrics

        if name == "rsi":
            period = int(getattr(self.strategy, "period", 14))
            overbought = float(getattr(self.strategy, "overbought", 70.0))
            oversold = float(getattr(self.strategy, "oversold", 30.0))
            if len(closes) < period + 1:
                return f"waiting candles: need >= {period + 1}, got {len(closes)}", metrics
            gains = 0.0
            losses = 0.0
            for i in range(-period, 0):
                d = closes[i] - closes[i - 1]
                if d > 0:
                    gains += d
                else:
                    losses += -d
            if losses == 0:
                rsi = 100.0
            else:
                rs = (gains / period) / (losses / period)
                rsi = 100.0 - (100.0 / (1.0 + rs))
            metrics = {
                "rsi": round(rsi, 4),
                "overbought": overbought,
                "oversold": oversold,
            }
            if signal == "buy":
                return "RSI entered oversold zone", metrics
            if signal == "sell":
                return "RSI entered overbought zone", metrics
            return "RSI in neutral zone", metrics

        if name == "ema_cross":
            fast = int(getattr(self.strategy, "fast", 9))
            slow = int(getattr(self.strategy, "slow", 21))
            if len(closes) < slow + 2:
                return f"waiting candles: need >= {slow + 2}, got {len(closes)}", metrics
            kf = 2 / (fast + 1)
            ks = 2 / (slow + 1)
            fast_ema = closes[0]
            slow_ema = closes[0]
            prev_fast = fast_ema
            prev_slow = slow_ema
            for p in closes[1:]:
                prev_fast = fast_ema
                prev_slow = slow_ema
                fast_ema = p * kf + fast_ema * (1 - kf)
                slow_ema = p * ks + slow_ema * (1 - ks)
            metrics = {
                "fast_ema_prev": round(prev_fast, 6),
                "slow_ema_prev": round(prev_slow, 6),
                "fast_ema": round(fast_ema, 6),
                "slow_ema": round(slow_ema, 6),
                "fast": fast,
                "slow": slow,
            }
            if signal == "buy":
                return "EMA golden cross detected", metrics
            if signal == "sell":
                return "EMA death cross detected", metrics
            return "no EMA cross on latest bar", metrics

        if name == "grid":
            grid_pct = float(getattr(self.strategy, "grid_pct", 0.01))
            anchor_price = getattr(self.strategy, "anchor_price", None)
            price = closes[-1]
            if anchor_price is None:
                return "grid anchor initialized, waiting movement", {"price": round(price, 6), "grid_pct": grid_pct}
            upper = anchor_price * (1.0 + grid_pct)
            lower = anchor_price * (1.0 - grid_pct)
            metrics = {
                "price": round(price, 6),
                "anchor_price": round(anchor_price, 6),
                "upper": round(upper, 6),
                "lower": round(lower, 6),
                "grid_pct": grid_pct,
            }
            if signal == "buy":
                return "price touched/broke lower grid", metrics
            if signal == "sell":
                return "price touched/broke upper grid", metrics
            return "price still inside current grid band", metrics

        if name == "hf_scalp":
            lookback = int(getattr(self.strategy, "lookback", 4))
            entry_bps = float(getattr(self.strategy, "entry_bps", 3.0))
            exit_bps = float(getattr(self.strategy, "exit_bps", 2.0))
            if len(closes) < lookback + 2:
                return f"waiting candles: need >= {lookback + 2}, got {len(closes)}", metrics
            current = float(closes[-1])
            base = float(closes[-1 - lookback])
            if base <= 0:
                return "invalid base price for hf_scalp", metrics
            ret_bps = (current - base) / base * 10000.0
            metrics = {
                "lookback": lookback,
                "ret_bps": round(ret_bps, 4),
                "entry_bps": entry_bps,
                "exit_bps": exit_bps,
            }
            if signal == "buy":
                return "short-window momentum breakout buy", metrics
            if signal == "sell":
                return "short-window pullback sell", metrics
            return "momentum threshold not reached", metrics

        if name == "smart_adaptive":
            import statistics

            fast = int(getattr(self.strategy, "fast", 12))
            slow = int(getattr(self.strategy, "slow", 48))
            rsi_period = int(getattr(self.strategy, "rsi_period", 14))
            bb_window = int(getattr(self.strategy, "bb_window", 20))
            vol_window = int(getattr(self.strategy, "vol_window", 20))
            trend_threshold_pct = float(getattr(self.strategy, "trend_threshold_pct", 0.18))
            max_vol_pct = float(getattr(self.strategy, "max_vol_pct", 2.8))
            min_need = max(slow + 2, rsi_period + 2, bb_window + 2, vol_window + 2)
            if len(closes) < min_need:
                return f"waiting candles: need >= {min_need}, got {len(closes)}", metrics

            # EMA
            kf = 2 / (fast + 1)
            ks = 2 / (slow + 1)
            ema_f = closes[0]
            ema_s = closes[0]
            for p in closes[1:]:
                ema_f = p * kf + ema_f * (1 - kf)
                ema_s = p * ks + ema_s * (1 - ks)
            trend_pct = 0.0 if ema_s == 0 else ((ema_f - ema_s) / ema_s) * 100.0

            # RSI
            gains = 0.0
            losses = 0.0
            for i in range(-rsi_period, 0):
                d = closes[i] - closes[i - 1]
                if d > 0:
                    gains += d
                else:
                    losses += -d
            if losses == 0:
                rsi = 100.0
            else:
                rs = (gains / rsi_period) / (losses / rsi_period)
                rsi = 100.0 - (100.0 / (1.0 + rs))

            # z-score
            chunk = closes[-bb_window:]
            mean = statistics.mean(chunk)
            std = statistics.pstdev(chunk) if len(chunk) > 1 else 0.0
            price = closes[-1]
            z = 0.0 if std <= 1e-12 else (price - mean) / std

            # volatility
            ret_chunk = closes[-(vol_window + 1):]
            rets = []
            for i in range(1, len(ret_chunk)):
                base = ret_chunk[i - 1]
                if base <= 0:
                    continue
                rets.append((ret_chunk[i] - base) / base)
            vol_pct = (statistics.pstdev(rets) * 100.0) if len(rets) > 1 else 0.0

            regime = "trend" if abs(trend_pct) >= trend_threshold_pct and vol_pct < max_vol_pct else "range"
            metrics = {
                "regime": regime,
                "price": round(price, 6),
                "trend_pct": round(trend_pct, 4),
                "rsi": round(rsi, 4),
                "zscore": round(z, 4),
                "vol_pct": round(vol_pct, 4),
                "fast": fast,
                "slow": slow,
            }
            if signal == "buy":
                return f"{regime} regime buy trigger", metrics
            if signal == "sell":
                return f"{regime} regime sell trigger", metrics
            return f"{regime} regime hold", metrics

        return "no strategy-specific analyzer", metrics

    def _estimate_mode_equity(self) -> float:
        if self.state.mode == "paper":
            return max(settings.paper_equity_usdt, 1.0)

        try:
            bal = self.client.fetch_balance()
            total = bal.get("total", {}) if isinstance(bal, dict) else {}
            usdt = float(total.get("USDT") or 0.0)
            if usdt > 0:
                return usdt
        except Exception:
            pass
        return max(settings.paper_equity_usdt, 1.0)

    def _calc_buy_amount(self, latest_price: float) -> float:
        conf = self.state.order_sizing
        equity = self._estimate_mode_equity()
        open_count = max(1, self._active_bot_counter(self.state.mode))
        slots = max(1, conf.max_open_trades)

        dynamic_usdt = equity * max(0.05, min(conf.tradable_balance_ratio, 1.0)) / max(open_count, slots)
        risk_usdt = equity * max(0.0, conf.risk_per_trade_pct) / 100.0
        stop_loss = max(conf.stop_loss_pct, 0.01) / 100.0
        risk_position_usdt = risk_usdt / stop_loss

        if conf.amount_mode == "dynamic":
            target_usdt = min(dynamic_usdt, risk_position_usdt)
        else:
            target_usdt = conf.fixed_order_usdt

        target_usdt = min(target_usdt, conf.max_order_usdt, settings.risk_max_position_usdt)
        current_symbol_exposure = self.state.position_qty * latest_price
        remain_symbol_exposure = max(0.0, conf.max_symbol_exposure_usdt - current_symbol_exposure)
        target_usdt = min(target_usdt, remain_symbol_exposure)

        if target_usdt <= 1e-8:
            return 0.0
        return round(target_usdt / max(latest_price, 1e-8), 8)

    def _place(self, signal: Signal, latest_price: float) -> None:
        if signal == "buy":
            amount = self._calc_buy_amount(latest_price)
            if amount <= 0:
                self.state.last_error = "Risk blocked: target buy amount is zero"
                return
            req = OrderRequest(
                symbol=self.state.symbol,
                side="buy",
                amount=amount,
                reason=f"{self.state.strategy_name} buy",
            )
        elif signal == "sell":
            amount = round(max(self.state.position_qty, 0.0), 8)
            if amount <= 0:
                return
            req = OrderRequest(
                symbol=self.state.symbol,
                side="sell",
                amount=amount,
                reason=f"{self.state.strategy_name} sell",
            )
        else:
            return

        self._execute_and_apply(req, latest_price)

    def _bootstrap_market_entry(self) -> None:
        ticker = self.client.fetch_ticker(self.state.symbol)
        latest_price = float(ticker.get("last") or 0.0)
        if latest_price <= 0:
            raise RuntimeError("bootstrap entry failed: invalid latest price")
        amount = self._calc_buy_amount(latest_price)
        if amount <= 0:
            raise RuntimeError("bootstrap entry blocked: amount <= 0")
        req = OrderRequest(
            symbol=self.state.symbol,
            side="buy",
            amount=amount,
            reason=f"{self.state.strategy_name} bootstrap buy",
        )
        ok = self._execute_and_apply(req, latest_price)
        self.state.initial_entry_done = ok

    def _execute_and_apply(self, req: OrderRequest, latest_price: float) -> bool:
        ok, reason = self.risk.allow_order(
            req,
            latest_price,
            max_notional_usdt=self.state.order_sizing.max_order_usdt,
            mode=self.state.mode,
        )
        if not ok:
            self.state.last_error = f"Risk blocked: {reason}"
            self.notifier.send("风控拦截", self.state.last_error)
            return False

        executed = self.client.create_market_order(req, mode=self.state.mode)
        self.storage.add_order(executed)
        self.state.history.insert(0, asdict(executed))
        self.state.history = self.state.history[:100]
        self.state.trades_count += 1
        self.state.last_error = ""

        if executed.side == "buy":
            self.state.buy_count += 1
            old_qty = self.state.position_qty
            new_qty = old_qty + executed.amount
            if new_qty > 0:
                self.state.avg_entry_price = (
                    (old_qty * self.state.avg_entry_price) + (executed.amount * executed.price)
                ) / new_qty
            self.state.position_qty = new_qty
        else:
            self.state.sell_count += 1
            close_qty = min(self.state.position_qty, executed.amount)
            if close_qty > 0:
                self.state.realized_pnl += (executed.price - self.state.avg_entry_price) * close_qty
            self.state.position_qty = max(0.0, self.state.position_qty - executed.amount)
            if self.state.position_qty <= 1e-9:
                self.state.position_qty = 0.0
                self.state.avg_entry_price = 0.0

        self._update_unrealized(executed.price)
        return True

    def _update_unrealized(self, latest_price: float) -> None:
        if self.state.position_qty > 0 and self.state.avg_entry_price > 0:
            self.state.unrealized_pnl = (latest_price - self.state.avg_entry_price) * self.state.position_qty
        else:
            self.state.unrealized_pnl = 0.0
        self.state.total_pnl = self.state.realized_pnl + self.state.unrealized_pnl

    def snapshot(self) -> dict[str, Any]:
        return asdict(self.state)


class TradingEngine:
    def __init__(self) -> None:
        self.client = BinanceClient()
        self.storage = Storage()
        self.risk = RiskManager(self.storage)
        self.notifier = FeishuNotifier()
        self._bots: dict[str, BotRuntime] = {}
        self._lock = threading.Lock()

    def _default_strategy_params(self, strategy_name: str) -> dict[str, Any]:
        if strategy_name == "ema_cross":
            return {"fast": settings.ema_fast, "slow": settings.ema_slow}
        if strategy_name == "bollinger":
            return {"window": settings.boll_window, "std_mult": settings.boll_std_mult}
        if strategy_name == "rsi":
            return {
                "period": settings.rsi_period,
                "overbought": settings.rsi_overbought,
                "oversold": settings.rsi_oversold,
            }
        if strategy_name == "grid":
            return {"grid_pct": settings.grid_pct}
        if strategy_name == "hf_scalp":
            return {"lookback": 4, "entry_bps": 3.0, "exit_bps": 2.0}
        return {}

    def _build_strategy(self, strategy_name: str, params: dict[str, Any] | None) -> tuple[Strategy, dict[str, Any]]:
        strategy = make_strategy(strategy_name)
        full_params = self._default_strategy_params(strategy_name)
        if params:
            full_params.update(params)
        strategy.update_params(full_params)
        return strategy, full_params

    def _normalize_order_sizing(self, cfg: dict[str, Any] | None) -> OrderSizingConfig:
        raw = cfg or {}
        amount_mode = str(raw.get("amount_mode", "fixed")).lower().strip()
        if amount_mode not in {"fixed", "dynamic"}:
            amount_mode = "fixed"

        return OrderSizingConfig(
            amount_mode=amount_mode,
            fixed_order_usdt=float(raw.get("fixed_order_usdt", 100.0)),
            max_order_usdt=float(raw.get("max_order_usdt", 300.0)),
            risk_per_trade_pct=float(raw.get("risk_per_trade_pct", 0.5)),
            stop_loss_pct=float(raw.get("stop_loss_pct", 2.0)),
            tradable_balance_ratio=float(raw.get("tradable_balance_ratio", 0.95)),
            max_open_trades=max(1, int(raw.get("max_open_trades", 5))),
            max_symbol_exposure_usdt=float(raw.get("max_symbol_exposure_usdt", 800.0)),
            initial_entry_on_start=bool(raw.get("initial_entry_on_start", False)),
        )

    def _count_active_bots(self, mode: str | None = None) -> int:
        n = 0
        for bot in self._bots.values():
            if not bot.state.running:
                continue
            if mode and bot.state.mode != mode:
                continue
            n += 1
        return n

    def _bot_key(self, symbol: str, mode: str, strategy_name: str) -> str:
        symbol_key = symbol.replace("/", "_").lower()
        return f"{mode}:{symbol_key}:{strategy_name}"

    def update_exchange_settings(
        self,
        *,
        trade_mode: str,
        live_confirm: bool,
        use_binance_testnet: bool,
        binance_api_key: str,
        binance_api_secret: str,
    ) -> None:
        with self._lock:
            if any(bot.state.running for bot in self._bots.values()):
                raise RuntimeError("Please stop all bots before updating exchange settings.")
            settings.trade_mode = trade_mode
            settings.live_confirm = live_confirm
            settings.use_binance_testnet = use_binance_testnet
            settings.binance_api_key = binance_api_key
            settings.binance_api_secret = binance_api_secret
            self.client = BinanceClient()

    def start(
        self,
        *,
        symbol: str,
        strategy_name: str,
        strategy_params: dict[str, Any] | None,
        mode: str,
        order_sizing: dict[str, Any] | None = None,
    ) -> str:
        mode = mode.lower().strip()
        if mode not in {"paper", "live"}:
            raise ValueError("mode must be paper or live")
        if mode == "live" and not settings.live_confirm:
            raise RuntimeError("Live mode blocked: set LIVE_CONFIRM=true in settings.")

        key = self._bot_key(symbol=symbol, mode=mode, strategy_name=strategy_name)
        with self._lock:
            bot = self._bots.get(key)
            if bot and bot.state.running:
                if (
                    order_sizing
                    and bool(order_sizing.get("initial_entry_on_start"))
                    and not bot.state.initial_entry_done
                    and bot.state.position_qty <= 0
                ):
                    bot.state.order_sizing.initial_entry_on_start = True
                    try:
                        bot._bootstrap_market_entry()
                    except Exception as e:  # noqa: BLE001
                        bot.state.last_error = str(e)
                        self.notifier.send("首单补触发失败", f"bot={bot.bot_id}, err={e}")
                return key

            strategy, merged_params = self._build_strategy(strategy_name, strategy_params)
            bot = BotRuntime(
                bot_id=key,
                symbol=symbol,
                mode=mode,
                strategy=strategy,
                strategy_params=merged_params,
                order_sizing=self._normalize_order_sizing(order_sizing),
                client=self.client,
                storage=self.storage,
                risk=self.risk,
                notifier=self.notifier,
                active_bot_counter=self._count_active_bots,
            )
            self._bots[key] = bot
            bot.start()
            return key

    def stop(
        self,
        *,
        bot_id: str | None = None,
        mode: str | None = None,
        symbol: str | None = None,
    ) -> int:
        stopped = 0
        with self._lock:
            if bot_id:
                bot = self._bots.get(bot_id)
                if bot and bot.state.running:
                    bot.stop()
                    stopped += 1
                return stopped

            for bot in self._bots.values():
                if not bot.state.running:
                    continue
                if mode and bot.state.mode != mode:
                    continue
                if symbol and bot.state.symbol != symbol:
                    continue
                bot.stop()
                stopped += 1
        return stopped

    def list_active_bots(self, mode: str | None = None) -> list[dict[str, Any]]:
        bots: list[dict[str, Any]] = []
        with self._lock:
            for bot in self._bots.values():
                s = bot.state
                if mode and s.mode != mode:
                    continue
                if not s.running:
                    continue
                bots.append(
                    {
                        "bot_id": s.bot_id,
                        "symbol": s.symbol,
                        "mode": s.mode,
                        "strategy_name": s.strategy_name,
                        "buy_count": s.buy_count,
                        "sell_count": s.sell_count,
                        "trades_count": s.trades_count,
                        "last_price": s.last_price,
                        "position_qty": s.position_qty,
                        "pnl": s.total_pnl,
                        "running": s.running,
                        "last_signal": s.last_signal,
                        "last_error": s.last_error,
                        "signal_reason": s.signal_reason,
                        "signal_metrics": s.signal_metrics,
                        "amount_mode": s.order_sizing.amount_mode,
                        "fixed_order_usdt": s.order_sizing.fixed_order_usdt,
                        "max_order_usdt": s.order_sizing.max_order_usdt,
                        "initial_entry_on_start": s.order_sizing.initial_entry_on_start,
                        "initial_entry_done": s.initial_entry_done,
                    }
                )
        bots.sort(key=lambda x: x["symbol"])
        return bots

    def snapshot(self, mode: str | None = None) -> dict[str, Any]:
        current_mode = (mode or settings.trade_mode).lower().strip()
        active_bots = self.list_active_bots(current_mode)
        selected = active_bots[0] if active_bots else None
        recent_orders = self.storage.recent_orders(limit=30, mode=current_mode)

        return {
            "running": bool(active_bots),
            "symbol": selected["symbol"] if selected else settings.default_symbol,
            "last_signal": selected["last_signal"] if selected else "hold",
            "signal_reason": selected["signal_reason"] if selected else "",
            "signal_metrics": selected["signal_metrics"] if selected else {},
            "last_price": selected["last_price"] if selected else 0.0,
            "position_qty": selected["position_qty"] if selected else 0.0,
            "trades_count": selected["trades_count"] if selected else 0,
            "last_tick": datetime.utcnow().isoformat(),
            "last_error": selected["last_error"] if selected else "",
            "mode": current_mode,
            "strategy_name": selected["strategy_name"] if selected else settings.strategy_name,
            "strategy_params": {},
            "initial_entry_on_start": selected["initial_entry_on_start"] if selected else False,
            "initial_entry_done": selected["initial_entry_done"] if selected else False,
            "strategies": list_strategies(),
            "recent_orders": recent_orders,
            "active_bots": active_bots,
        }
