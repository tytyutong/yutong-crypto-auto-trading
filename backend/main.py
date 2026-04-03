from pathlib import Path
from typing import Any
import math
import itertools
import threading
import uuid
import random
from datetime import datetime, timedelta, timezone

from dotenv import set_key
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.config import settings
from backend.trading.backtest import run_backtest
from backend.trading.engine import TradingEngine
from backend.trading.exchange.binance_client import BinanceClient
from backend.trading.strategy.profile_store import StrategyProfileStore
from backend.trading.strategy.registry import list_strategies

app = FastAPI(title="Auto Crypto Quant - Binance", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = TradingEngine()
strategy_profiles = StrategyProfileStore()
web_dir = Path(__file__).parent / "trading" / "web"
frontend_file = web_dir / "index.html"
settings_file = web_dir / "settings.html"
env_file = Path(".env")
backtest_jobs: dict[str, dict[str, Any]] = {}
backtest_jobs_lock = threading.Lock()

DEFAULT_SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "BNB/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "ADA/USDT",
    "TRX/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "LTC/USDT",
    "DOT/USDT",
]


class StartRequest(BaseModel):
    symbol: str = Field(default=settings.default_symbol, description="e.g. BTC/USDT")
    strategy_name: str = Field(default=settings.strategy_name, description="ema_cross/bollinger/rsi/grid")
    strategy_profile_id: str | None = None
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    timeframe: str = Field(default=settings.timeframe, description="signal timeframe, e.g. 1m/5m/1h")
    signal_kline_limit: int = Field(default=200, ge=50, le=1000)
    trade_mode: str = Field(default=settings.trade_mode, description="paper/live")
    order_sizing: dict[str, Any] = Field(default_factory=dict)


class StopRequest(BaseModel):
    bot_id: str | None = None
    symbol: str | None = None
    trade_mode: str | None = None


class BacktestRequest(BaseModel):
    symbol: str = Field(default=settings.default_symbol)
    timeframe: str = Field(default=settings.timeframe)
    strategy_name: str = Field(default=settings.strategy_name)
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=1000, ge=100, le=5000)
    range_days: int = Field(default=30, ge=0, le=365)
    start_time_ms: int | None = Field(default=None, gt=0)
    end_time_ms: int | None = Field(default=None, gt=0)
    initial_capital: float = Field(default=10000.0, gt=0)
    order_size_usdt: float = Field(default=settings.risk_max_position_usdt, gt=0)
    fee_rate: float = Field(default=0.001, ge=0, le=0.01)
    order_sizing: dict[str, Any] = Field(default_factory=dict)


class BacktestOptimizeRequest(BacktestRequest):
    top_k: int = Field(default=300, ge=1, le=300)
    max_candidates: int = Field(default=120, ge=10, le=300)


class OhlcvRequest(BaseModel):
    symbol: str = Field(default=settings.default_symbol)
    timeframe: str = Field(default=settings.timeframe)
    limit: int = Field(default=300, ge=50, le=1000)


class TickerRequest(BaseModel):
    symbol: str = Field(default=settings.default_symbol)


class ExchangeSettingsRequest(BaseModel):
    exchange_name: str = Field(default="binance")
    trade_mode: str = Field(default="paper")
    live_confirm: bool = False
    use_binance_testnet: bool = False
    binance_api_key: str = ""
    binance_api_secret: str = ""
    save_to_env: bool = True


class StrategyProfileCreateRequest(BaseModel):
    name: str
    strategy_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class StrategyProfileUpdateRequest(BaseModel):
    name: str | None = None
    strategy_name: str | None = None
    params: dict[str, Any] | None = None
    enabled: bool | None = None


@app.get("/")
def root() -> FileResponse:
    return FileResponse(frontend_file)


@app.get("/settings")
def settings_page() -> FileResponse:
    return FileResponse(settings_file)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/state")
def state(mode: str | None = None) -> dict:
    return engine.snapshot(mode=mode)


@app.get("/api/bots")
def bots(mode: str | None = None) -> dict:
    return {"bots": engine.list_active_bots(mode=mode)}


@app.get("/api/strategies")
def strategies() -> dict:
    return {
        "strategies": list_strategies(),
        "enabled_profiles": strategy_profiles.list(enabled_only=True),
    }


@app.get("/api/strategy-profiles")
def get_strategy_profiles(enabled_only: bool = False) -> dict:
    return {"profiles": strategy_profiles.list(enabled_only=enabled_only)}


@app.post("/api/strategy-profiles")
def create_strategy_profile(req: StrategyProfileCreateRequest) -> dict:
    try:
        row = strategy_profiles.create(
            name=req.name,
            strategy_name=req.strategy_name,
            params=req.params,
            enabled=req.enabled,
        )
        return {"ok": True, "profile": row}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.put("/api/strategy-profiles/{profile_id}")
def update_strategy_profile(profile_id: str, req: StrategyProfileUpdateRequest) -> dict:
    try:
        row = strategy_profiles.update(
            profile_id,
            name=req.name,
            strategy_name=req.strategy_name,
            params=req.params,
            enabled=req.enabled,
        )
        return {"ok": True, "profile": row}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/strategy-profiles/{profile_id}/enable")
def enable_strategy_profile(profile_id: str) -> dict:
    try:
        row = strategy_profiles.update(profile_id, enabled=True)
        return {"ok": True, "profile": row}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/strategy-profiles/{profile_id}/disable")
def disable_strategy_profile(profile_id: str) -> dict:
    try:
        row = strategy_profiles.update(profile_id, enabled=False)
        return {"ok": True, "profile": row}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.delete("/api/strategy-profiles/{profile_id}")
def delete_strategy_profile(profile_id: str) -> dict:
    try:
        strategy_profiles.delete(profile_id)
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/symbols")
def symbols(quote: str = "USDT", limit: int = 500) -> dict:
    try:
        remote = engine.client.list_symbols(quote=quote, limit=limit)
        combined = DEFAULT_SYMBOLS + [s for s in remote if s not in DEFAULT_SYMBOLS]
        return {"symbols": combined[:limit]}
    except Exception as e:  # noqa: BLE001
        return {"symbols": DEFAULT_SYMBOLS, "warning": str(e)}


@app.post("/api/ohlcv")
def ohlcv(req: OhlcvRequest) -> dict:
    try:
        data = engine.client.fetch_ohlcv(req.symbol, req.timeframe, req.limit)
        return {"symbol": req.symbol, "timeframe": req.timeframe, "ohlcv": data}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/ticker")
def ticker(req: TickerRequest) -> dict:
    try:
        data = engine.client.fetch_ticker(req.symbol)
        return {"symbol": req.symbol, "ticker": data}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/start")
def start(req: StartRequest) -> dict:
    try:
        strategy_name = req.strategy_name
        strategy_params = dict(req.strategy_params)
        timeframe = req.timeframe
        signal_kline_limit = int(req.signal_kline_limit)

        if req.strategy_profile_id:
            profile = strategy_profiles.get(req.strategy_profile_id)
            if not profile.get("enabled"):
                raise ValueError("selected strategy profile is disabled")
            strategy_name = str(profile.get("strategy_name") or "").strip().lower()
            strategy_params = dict(profile.get("params") or {})
            if req.strategy_params:
                strategy_params.update(req.strategy_params)

        # Allow execution-level parameters to be configured inside strategy JSON.
        # Example:
        # {
        #   "fast": 12,
        #   "slow": 48,
        #   "timeframe": "10m",
        #   "signal_kline_limit": 300
        # }
        tf_in_params = strategy_params.pop("timeframe", None)
        if tf_in_params is not None:
            timeframe = str(tf_in_params).strip()
        kline_limit_in_params = strategy_params.pop("signal_kline_limit", None)
        if kline_limit_in_params is not None:
            signal_kline_limit = int(kline_limit_in_params)

        bot_id = engine.start(
            symbol=req.symbol,
            strategy_name=strategy_name,
            strategy_params=strategy_params,
            timeframe=timeframe,
            signal_kline_limit=signal_kline_limit,
            mode=req.trade_mode,
            order_sizing=req.order_sizing,
        )
        return {
            "ok": True,
            "bot_id": bot_id,
            "message": f"bot started on {req.symbol} ({strategy_name}, {req.trade_mode}, tf={timeframe}, kline={signal_kline_limit})",
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/stop")
def stop(req: StopRequest | None = None) -> dict:
    request = req or StopRequest()
    stopped = engine.stop(bot_id=request.bot_id, mode=request.trade_mode, symbol=request.symbol)
    return {"ok": True, "message": f"stopped {stopped} bot(s)", "stopped": stopped}


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


def _timeframe_to_ms(timeframe: str) -> int:
    tf = (timeframe or "").strip()
    if len(tf) < 2:
        raise ValueError(f"invalid timeframe: {timeframe}")
    unit = tf[-1]
    value = int(tf[:-1])
    unit_ms = {
        "s": 1000,
        "m": 60 * 1000,
        "h": 60 * 60 * 1000,
        "d": 24 * 60 * 60 * 1000,
        "w": 7 * 24 * 60 * 60 * 1000,
        "M": 30 * 24 * 60 * 60 * 1000,
    }.get(unit)
    if unit_ms is None:
        raise ValueError(f"unsupported timeframe unit: {timeframe}")
    return value * unit_ms


def _prepare_backtest_ohlcv(req: BacktestRequest) -> tuple[list[list[float]], dict[str, Any]]:
    fetch_meta: dict[str, Any] = {
        "requested_limit": req.limit,
        "range_days": req.range_days,
        "start_time_ms": req.start_time_ms,
        "end_time_ms": req.end_time_ms,
        "truncated_by_limit": False,
        "requested_candles": req.limit,
    }
    tf_ms = _timeframe_to_ms(req.timeframe)
    if req.start_time_ms and req.end_time_ms:
        if req.end_time_ms <= req.start_time_ms:
            raise ValueError("自定义结束时间必须大于开始时间")
        start_ms = int(req.start_time_ms)
        end_ms = int(req.end_time_ms)
        requested_candles = max(100, int(math.ceil((end_ms - start_ms) / tf_ms)) + 5)
        fetch_limit = min(requested_candles, 5000)
        fetch_meta["requested_candles"] = requested_candles
        fetch_meta["truncated_by_limit"] = requested_candles > fetch_limit
        ohlcv = engine.client.fetch_ohlcv(
            req.symbol,
            req.timeframe,
            limit=fetch_limit,
            start_time_ms=start_ms,
            end_time_ms=end_ms,
        )
    elif req.range_days > 0:
        now = datetime.now(timezone.utc)
        start_at = now - timedelta(days=req.range_days)
        start_ms = int(start_at.timestamp() * 1000)
        end_ms = int(now.timestamp() * 1000)
        requested_candles = max(100, int(math.ceil((end_ms - start_ms) / tf_ms)) + 5)
        fetch_limit = min(requested_candles, 5000)
        fetch_meta["requested_candles"] = requested_candles
        fetch_meta["truncated_by_limit"] = requested_candles > fetch_limit
        ohlcv = engine.client.fetch_ohlcv(
            req.symbol,
            req.timeframe,
            limit=fetch_limit,
            start_time_ms=start_ms,
            end_time_ms=end_ms,
        )
    else:
        ohlcv = engine.client.fetch_ohlcv(req.symbol, req.timeframe, limit=req.limit)
    return ohlcv, fetch_meta


def _backtest_result_to_dict(result: Any, ohlcv: list[list[float]], fetch_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy_name": result.strategy_name,
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "candles": result.candles,
        "trades": result.trades,
        "win_rate": result.win_rate,
        "total_pnl": result.total_pnl,
        "total_return_pct": result.total_return_pct,
        "max_drawdown_pct": result.max_drawdown_pct,
        "initial_capital": result.initial_capital,
        "final_equity": result.final_equity,
        "equity_curve": result.equity_curve,
        "price_curve": result.price_curve,
        "orders": result.orders,
        "diagnostics": result.diagnostics,
        "start_time": int(ohlcv[0][0]) if ohlcv else None,
        "end_time": int(ohlcv[-1][0]) if ohlcv else None,
        "fetch_meta": fetch_meta,
    }


def _build_param_candidates(strategy_name: str, base_params: dict[str, Any], max_candidates: int) -> list[dict[str, Any]]:
    s = strategy_name.strip().lower()
    grid: dict[str, list[Any]]
    if s == "ema_cross":
        grid = {"fast": [6, 9, 12, 15], "slow": [21, 34, 55]}
    elif s == "bollinger":
        grid = {"window": [14, 20, 26, 34], "std_mult": [1.6, 2.0, 2.4]}
    elif s == "rsi":
        grid = {"period": [7, 14, 21], "overbought": [65, 70, 75], "oversold": [25, 30, 35]}
    elif s == "grid":
        grid = {"grid_pct": [0.003, 0.005, 0.008, 0.01, 0.015, 0.02]}
    elif s == "hf_scalp":
        grid = {"lookback": [2, 4, 6, 8], "entry_bps": [2.0, 3.0, 4.0, 5.0], "exit_bps": [1.0, 2.0, 3.0]}
    elif s == "smart_adaptive":
        grid = {
            "fast": [8, 12, 16],
            "slow": [36, 48, 72],
            "trend_threshold_pct": [0.12, 0.18, 0.25],
            "trend_pullback_rsi": [40, 46, 52],
            "trend_takeprofit_rsi": [64, 69, 74],
            "trend_pullback_z": [0.2, 0.35, 0.5],
            "trend_takeprofit_z": [1.0, 1.25, 1.6],
            "range_entry_z": [0.9, 1.15, 1.4],
            "range_exit_z": [0.55, 0.75, 0.95],
            "max_vol_pct": [2.0, 2.8, 3.6],
            "cooldown_bars": [1, 2, 4],
        }
    else:
        return [dict(base_params)]

    keys = list(grid.keys())
    combos = [dict(zip(keys, values)) for values in itertools.product(*(grid[k] for k in keys))]
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _try_push(params: dict[str, Any]) -> None:
        k = str(sorted(params.items()))
        if k in seen:
            return
        seen.add(k)
        if s == "ema_cross" and int(params.get("fast", 0)) >= int(params.get("slow", 0)):
            return
        if s == "rsi" and float(params.get("oversold", 0)) >= float(params.get("overbought", 100)):
            return
        if s == "smart_adaptive":
            if int(params.get("fast", 0)) >= int(params.get("slow", 0)):
                return
            if float(params.get("range_exit_z", 0.0)) >= float(params.get("range_entry_z", 1.0)):
                return
        candidates.append(params)

    _try_push(dict(base_params))
    for c in combos:
        merged = dict(base_params)
        merged.update(c)
        _try_push(merged)

    if len(candidates) <= max_candidates:
        return candidates

    # Keep base params, then sample the rest deterministically to avoid regular-stride bias.
    base_candidate = candidates[0]
    rest = candidates[1:]
    rnd = random.Random(42)
    sampled = rnd.sample(rest, k=max(0, min(len(rest), max_candidates - 1)))
    return [base_candidate, *sampled]


def _backtest_job_snapshot(job_id: str) -> dict[str, Any]:
    with backtest_jobs_lock:
        job = backtest_jobs.get(job_id)
        if not job:
            raise ValueError("job not found")
        return dict(job)


def _upsert_backtest_job(job_id: str, patch: dict[str, Any]) -> None:
    with backtest_jobs_lock:
        job = backtest_jobs.get(job_id)
        if not job:
            return
        job.update(patch)
        job["updated_at"] = datetime.utcnow().isoformat()


def _create_backtest_job(kind: str, req_payload: dict[str, Any]) -> str:
    job_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    with backtest_jobs_lock:
        backtest_jobs[job_id] = {
            "job_id": job_id,
            "kind": kind,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "request": req_payload,
            "result": None,
            "error": "",
        }
    return job_id


def _run_backtest_job_sync(req: BacktestRequest) -> dict[str, Any]:
    ohlcv, fetch_meta = _prepare_backtest_ohlcv(req)
    result = run_backtest(
        strategy_name=req.strategy_name,
        strategy_params=req.strategy_params,
        symbol=req.symbol,
        timeframe=req.timeframe,
        ohlcv=ohlcv,
        initial_capital=req.initial_capital,
        order_size_usdt=req.order_size_usdt,
        fee_rate=req.fee_rate,
        order_sizing=req.order_sizing,
    )
    return _backtest_result_to_dict(result, ohlcv, fetch_meta)


def _run_optimize_job_sync(req: BacktestOptimizeRequest) -> dict[str, Any]:
    ohlcv, fetch_meta = _prepare_backtest_ohlcv(req)
    base_params = dict(req.strategy_params or {})
    candidates = _build_param_candidates(req.strategy_name, base_params, req.max_candidates)
    if not candidates:
        raise ValueError("没有可用的参数组合")

    ranked: list[dict[str, Any]] = []
    profitable_ranked: list[dict[str, Any]] = []
    best_result = None
    best_score = -10**18
    for params in candidates:
        result = run_backtest(
            strategy_name=req.strategy_name,
            strategy_params=params,
            symbol=req.symbol,
            timeframe=req.timeframe,
            ohlcv=ohlcv,
            initial_capital=req.initial_capital,
            order_size_usdt=req.order_size_usdt,
            fee_rate=req.fee_rate,
            order_sizing=req.order_sizing,
        )
        score = float(result.total_return_pct) - float(result.max_drawdown_pct) * 0.25 + float(result.win_rate) * 0.02
        ranked.append(
            {
                "params": params,
                "score": round(score, 6),
                "total_return_pct": result.total_return_pct,
                "total_pnl": result.total_pnl,
                "max_drawdown_pct": result.max_drawdown_pct,
                "win_rate": result.win_rate,
                "trades": result.trades,
                "final_equity": result.final_equity,
            }
        )
        if float(result.total_return_pct) > 0:
            profitable_ranked.append(ranked[-1])
        if score > best_score:
            best_score = score
            best_result = result

    ranked.sort(key=lambda x: (float(x.get("score", 0.0)), float(x.get("total_return_pct", 0.0))), reverse=True)
    profitable_ranked.sort(
        key=lambda x: (float(x.get("total_return_pct", 0.0)), float(x.get("score", 0.0))),
        reverse=True,
    )
    # Return full ranking list so frontend can show all tested combinations.
    # Keep the top_k argument for compatibility, but default behavior now is full list.
    top_rows = profitable_ranked if profitable_ranked else ranked
    if best_result is None:
        raise ValueError("寻优失败：未获得有效结果")
    return {
        "strategy_name": req.strategy_name,
        "symbol": req.symbol,
        "timeframe": req.timeframe,
        "tested": len(candidates),
        "profitable_count": len(profitable_ranked),
        "top": top_rows,
        "best_result": _backtest_result_to_dict(best_result, ohlcv, fetch_meta),
    }


def _start_backtest_job(kind: str, req_payload: dict[str, Any], runner: callable) -> str:
    job_id = _create_backtest_job(kind, req_payload)

    def _task() -> None:
        _upsert_backtest_job(job_id, {"status": "running"})
        try:
            result = runner()
            _upsert_backtest_job(job_id, {"status": "completed", "result": result, "error": ""})
        except Exception as e:  # noqa: BLE001
            _upsert_backtest_job(job_id, {"status": "failed", "error": str(e)})

    t = threading.Thread(target=_task, daemon=True)
    t.start()
    return job_id


@app.get("/api/settings")
def get_settings() -> dict:
    return {
        "exchange_name": settings.exchange_name,
        "trade_mode": settings.trade_mode,
        "live_confirm": settings.live_confirm,
        "use_binance_testnet": settings.use_binance_testnet,
        "binance_api_key_masked": _mask_key(settings.binance_api_key),
        "has_api_secret": bool(settings.binance_api_secret),
    }


@app.post("/api/settings")
def update_settings(req: ExchangeSettingsRequest) -> dict:
    try:
        exchange_name = req.exchange_name.strip().lower()
        if exchange_name != "binance":
            raise ValueError("Only binance is supported currently.")
        if req.trade_mode not in {"paper", "live"}:
            raise ValueError("trade_mode must be paper or live")

        settings.exchange_name = exchange_name
        engine.update_exchange_settings(
            trade_mode=req.trade_mode,
            live_confirm=req.live_confirm,
            use_binance_testnet=req.use_binance_testnet,
            binance_api_key=req.binance_api_key.strip(),
            binance_api_secret=req.binance_api_secret.strip(),
        )

        if req.save_to_env:
            if not env_file.exists():
                env_file.write_text("", encoding="utf-8")
            set_key(str(env_file), "EXCHANGE_NAME", settings.exchange_name)
            set_key(str(env_file), "TRADE_MODE", settings.trade_mode)
            set_key(str(env_file), "LIVE_CONFIRM", str(settings.live_confirm).lower())
            set_key(str(env_file), "USE_BINANCE_TESTNET", str(settings.use_binance_testnet).lower())
            set_key(str(env_file), "BINANCE_API_KEY", settings.binance_api_key)
            set_key(str(env_file), "BINANCE_API_SECRET", settings.binance_api_secret)

        return {
            "ok": True,
            "message": "exchange settings updated",
            "exchange_name": settings.exchange_name,
            "trade_mode": settings.trade_mode,
            "use_binance_testnet": settings.use_binance_testnet,
            "binance_api_key_masked": _mask_key(settings.binance_api_key),
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/settings/test")
def test_settings(req: ExchangeSettingsRequest) -> dict:
    try:
        exchange_name = req.exchange_name.strip().lower()
        if exchange_name != "binance":
            raise ValueError("Only binance is supported currently.")
        api_key = req.binance_api_key.strip()
        api_secret = req.binance_api_secret.strip()
        if not api_key or not api_secret:
            raise ValueError("BINANCE_API_KEY and BINANCE_API_SECRET cannot be empty")

        result = BinanceClient.test_credentials(
            api_key=api_key,
            api_secret=api_secret,
            use_testnet=req.use_binance_testnet,
        )
        return {
            "ok": True,
            "message": "Binance API connected successfully",
            "exchange_name": exchange_name,
            "use_binance_testnet": req.use_binance_testnet,
            "last_price": result["last_price"],
            "asset_count": result["asset_count"],
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/backtest")
def backtest(req: BacktestRequest) -> dict:
    try:
        return {"ok": True, "result": _run_backtest_job_sync(req)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/backtest/optimize")
def backtest_optimize(req: BacktestOptimizeRequest) -> dict:
    try:
        return {"ok": True, **_run_optimize_job_sync(req)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/backtest/start")
def backtest_start(req: BacktestRequest) -> dict:
    try:
        req_payload = req.model_dump()
        job_id = _start_backtest_job("backtest", req_payload, lambda: _run_backtest_job_sync(req))
        return {"ok": True, "job_id": job_id, "status": "queued"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/backtest/optimize/start")
def backtest_optimize_start(req: BacktestOptimizeRequest) -> dict:
    try:
        req_payload = req.model_dump()
        job_id = _start_backtest_job("optimize", req_payload, lambda: _run_optimize_job_sync(req))
        return {"ok": True, "job_id": job_id, "status": "queued"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/backtest/jobs/{job_id}")
def get_backtest_job(job_id: str) -> dict:
    try:
        return {"ok": True, "job": _backtest_job_snapshot(job_id)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(e)) from e
