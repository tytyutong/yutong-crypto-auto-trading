from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from typing import Any

import ccxt  # type: ignore
import requests

from backend.config import settings
from backend.trading.models import ExecutedOrder, OrderRequest


class BinanceClient:
    def __init__(self) -> None:
        self.base_url = (
            "https://testnet.binance.vision"
            if settings.use_binance_testnet
            else "https://api.binance.com"
        )
        self.exchange = ccxt.binance(
            {
                "apiKey": settings.binance_api_key,
                "secret": settings.binance_api_secret,
                "enableRateLimit": True,
                "timeout": 30000,
            }
        )
        if settings.use_binance_testnet:
            self.exchange.set_sandbox_mode(True)
        self._symbol_filter_cache: dict[str, dict[str, float]] = {}

    @staticmethod
    def _to_exchange_symbol(symbol: str) -> str:
        return symbol.replace("/", "").strip().upper()

    def _public_get(self, path: str, params: dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"
        res = requests.get(url, params=params, timeout=15)
        res.raise_for_status()
        return res.json()

    def _fetch_ohlcv_raw(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 200,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[list[float]]:
        interval = timeframe
        raw_symbol = self._to_exchange_symbol(symbol)
        req_limit = max(1, int(limit))
        max_chunk = 1000
        out_rows: list[list[Any]] = []

        cursor_start = int(start_time_ms) if start_time_ms is not None else None
        cursor_end = int(end_time_ms) if end_time_ms is not None else None

        while len(out_rows) < req_limit:
            chunk = min(max_chunk, req_limit - len(out_rows))
            params: dict[str, Any] = {"symbol": raw_symbol, "interval": interval, "limit": chunk}
            if cursor_start is not None:
                params["startTime"] = cursor_start
            if cursor_end is not None:
                params["endTime"] = cursor_end
            rows = self._public_get("/api/v3/klines", params)
            if not rows:
                break
            out_rows.extend(rows)
            if len(rows) < chunk:
                break
            last_open = int(rows[-1][0])
            next_start = last_open + 1
            if cursor_start is not None and next_start <= cursor_start:
                break
            cursor_start = next_start
            if cursor_end is not None and cursor_start >= cursor_end:
                break
            if start_time_ms is None and end_time_ms is None:
                break

        out: list[list[float]] = []
        for r in out_rows[:req_limit]:
            out.append(
                [
                    float(r[0]),
                    float(r[1]),
                    float(r[2]),
                    float(r[3]),
                    float(r[4]),
                    float(r[5]),
                ]
            )
        return out

    @staticmethod
    def _aggregate_ohlcv(rows: list[list[float]], target_ms: int) -> list[list[float]]:
        buckets: dict[int, list[list[float]]] = {}
        for r in rows:
            ts = int(r[0])
            key = ts - (ts % target_ms)
            buckets.setdefault(key, []).append(r)
        out: list[list[float]] = []
        for key in sorted(buckets.keys()):
            chunk = buckets[key]
            if not chunk:
                continue
            open_ = chunk[0][1]
            high_ = max(x[2] for x in chunk)
            low_ = min(x[3] for x in chunk)
            close_ = chunk[-1][4]
            vol_ = sum(x[5] for x in chunk)
            out.append([float(key), float(open_), float(high_), float(low_), float(close_), float(vol_)])
        return out

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 200,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[list[float]]:
        tf = (timeframe or "").strip()
        if tf == "10m":
            # Binance has no native 10m interval; synthesize from 5m.
            raw = self._fetch_ohlcv_raw(
                symbol=symbol,
                timeframe="5m",
                limit=max(1, int(limit)) * 2,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
            merged = self._aggregate_ohlcv(raw, target_ms=10 * 60 * 1000)
            if len(merged) <= int(limit):
                return merged
            return merged[-int(limit):]

        return self._fetch_ohlcv_raw(
            symbol=symbol,
            timeframe=tf,
            limit=limit,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

    def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        raw_symbol = self._to_exchange_symbol(symbol)
        t = self._public_get("/api/v3/ticker/24hr", {"symbol": raw_symbol})
        return {
            "symbol": symbol,
            "last": float(t.get("lastPrice") or 0.0),
            "high": float(t.get("highPrice") or 0.0),
            "low": float(t.get("lowPrice") or 0.0),
            "bid": float(t.get("bidPrice") or 0.0),
            "ask": float(t.get("askPrice") or 0.0),
            "base_volume": float(t.get("volume") or 0.0),
            "quote_volume": float(t.get("quoteVolume") or 0.0),
            "price_change_pct": float(t.get("priceChangePercent") or 0.0),
        }

    def fetch_balance(self) -> dict[str, Any]:
        return self.exchange.fetch_balance()

    def list_symbols(self, quote: str = "USDT", limit: int = 500) -> list[str]:
        info = self._public_get("/api/v3/exchangeInfo", {})
        quote_upper = quote.upper().strip()
        symbols: list[str] = []
        for m in info.get("symbols", []):
            if m.get("status") != "TRADING":
                continue
            base = m.get("baseAsset")
            q = m.get("quoteAsset")
            if not base or not q:
                continue
            if str(q).upper() != quote_upper:
                continue
            symbols.append(f"{base}/{q}")
        symbols.sort()
        return symbols[:limit]

    def get_symbol_filters(self, symbol: str) -> dict[str, float]:
        raw_symbol = self._to_exchange_symbol(symbol)
        if raw_symbol in self._symbol_filter_cache:
            return self._symbol_filter_cache[raw_symbol]

        info = self._public_get("/api/v3/exchangeInfo", {"symbol": raw_symbol})
        symbols = info.get("symbols", [])
        if not symbols:
            raise RuntimeError(f"symbol not found in exchangeInfo: {symbol}")
        filters = symbols[0].get("filters", [])

        out = {
            "min_qty": 0.0,
            "max_qty": 0.0,
            "step_size": 0.0,
            "min_notional": 0.0,
        }
        for f in filters:
            ftype = f.get("filterType")
            if ftype == "LOT_SIZE":
                out["min_qty"] = float(f.get("minQty") or 0.0)
                out["max_qty"] = float(f.get("maxQty") or 0.0)
                out["step_size"] = float(f.get("stepSize") or 0.0)
            elif ftype in {"MIN_NOTIONAL", "NOTIONAL"}:
                min_n = f.get("minNotional")
                if min_n is not None:
                    out["min_notional"] = max(out["min_notional"], float(min_n))

        self._symbol_filter_cache[raw_symbol] = out
        return out

    @staticmethod
    def _round_qty_to_step(amount: float, step_size: float) -> float:
        if step_size <= 0:
            return amount
        amount_dec = Decimal(str(amount))
        step_dec = Decimal(str(step_size))
        steps = (amount_dec / step_dec).to_integral_value(rounding=ROUND_DOWN)
        rounded = steps * step_dec
        return float(rounded)

    @staticmethod
    def test_credentials(
        *,
        api_key: str,
        api_secret: str,
        use_testnet: bool = False,
    ) -> dict[str, Any]:
        ex = ccxt.binance(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
            }
        )
        if use_testnet:
            ex.set_sandbox_mode(True)

        ticker = ex.fetch_ticker("BTC/USDT")
        balance = ex.fetch_balance()
        return {
            "ok": True,
            "last_price": float(ticker.get("last") or 0.0),
            "asset_count": len(balance.get("total", {})),
        }

    def create_market_order(self, req: OrderRequest, *, mode: str | None = None) -> ExecutedOrder:
        order_mode = (mode or settings.trade_mode).lower().strip()
        ticker = self.fetch_ticker(req.symbol)
        last_price = float(ticker["last"])

        f = self.get_symbol_filters(req.symbol)
        amount = self._round_qty_to_step(req.amount, f["step_size"])
        if f["min_qty"] > 0 and amount < f["min_qty"]:
            raise RuntimeError(f"quantity {amount} < minQty {f['min_qty']}")
        if f["max_qty"] > 0 and amount > f["max_qty"]:
            raise RuntimeError(f"quantity {amount} > maxQty {f['max_qty']}")
        notional = amount * max(last_price, 0.0)
        if f["min_notional"] > 0 and notional < f["min_notional"]:
            raise RuntimeError(f"notional {notional:.4f} < minNotional {f['min_notional']}")

        if order_mode == "paper":
            return ExecutedOrder(
                symbol=req.symbol,
                side=req.side,
                amount=amount,
                price=last_price,
                mode="paper",
                exchange_order_id=f"PAPER-{int(datetime.utcnow().timestamp())}",
                reason=req.reason,
            )

        if order_mode == "live" and not settings.live_confirm:
            raise RuntimeError("Live mode blocked: set LIVE_CONFIRM=true in .env to enable.")

        order = self.exchange.create_order(
            symbol=req.symbol,
            type="market",
            side=req.side,
            amount=amount,
        )
        return ExecutedOrder(
            symbol=req.symbol,
            side=req.side,
            amount=float(order.get("amount") or req.amount),
            price=float(order.get("average") or order.get("price") or 0.0),
            mode="live",
            exchange_order_id=str(order.get("id", "")),
            reason=req.reason,
        )
