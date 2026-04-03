from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.trading.strategy.registry import make_strategy


@dataclass
class BacktestResult:
    strategy_name: str
    symbol: str
    timeframe: str
    candles: int
    trades: int
    win_rate: float
    total_pnl: float
    total_return_pct: float
    max_drawdown_pct: float
    initial_capital: float
    final_equity: float
    equity_curve: list[dict[str, Any]]
    price_curve: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    diagnostics: dict[str, Any]


@dataclass
class BacktestOrderSizing:
    amount_mode: str = "fixed"
    fixed_order_usdt: float = 100.0
    max_order_usdt: float = 300.0
    risk_per_trade_pct: float = 0.5
    stop_loss_pct: float = 2.0
    take_profit_pct: float = 0.0
    tradable_balance_ratio: float = 0.95
    max_open_trades: int = 5
    max_symbol_exposure_usdt: float = 800.0
    initial_entry_on_start: bool = False


def _normalize_order_sizing(order_size_usdt: float, raw: dict[str, Any] | None) -> BacktestOrderSizing:
    r = raw or {}
    amount_mode = str(r.get("amount_mode", "fixed")).lower().strip()
    if amount_mode not in {"fixed", "dynamic"}:
        amount_mode = "fixed"
    return BacktestOrderSizing(
        amount_mode=amount_mode,
        fixed_order_usdt=float(r.get("fixed_order_usdt", order_size_usdt)),
        max_order_usdt=float(r.get("max_order_usdt", max(order_size_usdt * 3, order_size_usdt))),
        risk_per_trade_pct=float(r.get("risk_per_trade_pct", 0.5)),
        stop_loss_pct=float(r.get("stop_loss_pct", 2.0)),
        take_profit_pct=float(r.get("take_profit_pct", 0.0)),
        tradable_balance_ratio=float(r.get("tradable_balance_ratio", 0.95)),
        max_open_trades=max(1, int(r.get("max_open_trades", 5))),
        max_symbol_exposure_usdt=float(r.get("max_symbol_exposure_usdt", 800.0)),
        initial_entry_on_start=bool(r.get("initial_entry_on_start", False)),
    )


def _calc_buy_notional(
    *,
    cash: float,
    equity: float,
    close_price: float,
    position_qty: float,
    conf: BacktestOrderSizing,
) -> float:
    if close_price <= 0:
        return 0.0

    dynamic_usdt = equity * max(0.05, min(conf.tradable_balance_ratio, 1.0)) / max(conf.max_open_trades, 1)
    risk_usdt = equity * max(conf.risk_per_trade_pct, 0.0) / 100.0
    stop_loss = max(conf.stop_loss_pct, 0.01) / 100.0
    risk_position_usdt = risk_usdt / stop_loss

    if conf.amount_mode == "dynamic":
        target_usdt = min(dynamic_usdt, risk_position_usdt)
    else:
        target_usdt = conf.fixed_order_usdt

    target_usdt = min(target_usdt, conf.max_order_usdt, cash)
    current_exposure = position_qty * close_price
    remain_exposure = max(0.0, conf.max_symbol_exposure_usdt - current_exposure)
    target_usdt = min(target_usdt, remain_exposure)
    return max(target_usdt, 0.0)


def run_backtest(
    *,
    strategy_name: str,
    strategy_params: dict[str, Any],
    symbol: str,
    timeframe: str,
    ohlcv: list[list[float]],
    initial_capital: float,
    order_size_usdt: float,
    fee_rate: float,
    order_sizing: dict[str, Any] | None = None,
) -> BacktestResult:
    strategy = make_strategy(strategy_name)
    strategy.update_params(strategy_params)
    conf = _normalize_order_sizing(order_size_usdt, order_sizing)

    cash = initial_capital
    position_qty = 0.0
    position_cost = 0.0
    wins = 0
    trades = 0
    max_equity = initial_capital
    max_drawdown_pct = 0.0
    orders: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    price_curve: list[dict[str, Any]] = []
    closes: list[float] = []
    bootstrapped = False

    for bar in ohlcv:
        ts = int(bar[0])
        high_price = float(bar[2])
        low_price = float(bar[3])
        close_price = float(bar[4])
        closes.append(close_price)
        price_curve.append(
            {
                "ts": datetime.utcfromtimestamp(ts / 1000).isoformat(),
                "close": round(close_price, 8),
            }
        )

        equity = cash + position_qty * close_price

        if conf.initial_entry_on_start and not bootstrapped and position_qty <= 0:
            notional = _calc_buy_notional(
                cash=cash,
                equity=equity,
                close_price=close_price,
                position_qty=position_qty,
                conf=conf,
            )
            if notional > 10:
                qty = notional / close_price
                fee = notional * fee_rate
                cash -= notional + fee
                position_qty += qty
                position_cost = notional + fee
                trades += 1
                orders.append(
                    {
                        "ts": ts,
                        "side": "buy",
                        "price": close_price,
                        "qty": qty,
                        "fee": fee,
                        "reason": "bootstrap",
                    }
                )
                bootstrapped = True

        exited_by_risk = False
        if position_qty > 0:
            avg_cost_price = (position_cost / position_qty) if position_qty > 0 else 0.0
            stop_loss_price = (
                avg_cost_price * (1.0 - max(conf.stop_loss_pct, 0.0) / 100.0)
                if conf.stop_loss_pct > 0
                else 0.0
            )
            take_profit_price = (
                avg_cost_price * (1.0 + max(conf.take_profit_pct, 0.0) / 100.0)
                if conf.take_profit_pct > 0
                else 0.0
            )

            # Conservative rule: when both touched in the same bar, execute stop-loss first.
            risk_exit_price = 0.0
            risk_reason = ""
            if stop_loss_price > 0 and low_price <= stop_loss_price:
                risk_exit_price = stop_loss_price
                risk_reason = "risk_stop_loss"
            elif take_profit_price > 0 and high_price >= take_profit_price:
                risk_exit_price = take_profit_price
                risk_reason = "risk_take_profit"

            if risk_exit_price > 0:
                notional = position_qty * risk_exit_price
                fee = notional * fee_rate
                cash += notional - fee
                pnl = (notional - fee) - position_cost
                if pnl > 0:
                    wins += 1
                trades += 1
                orders.append(
                    {
                        "ts": ts,
                        "side": "sell",
                        "price": risk_exit_price,
                        "qty": position_qty,
                        "fee": fee,
                        "pnl": pnl,
                        "reason": risk_reason,
                    }
                )
                position_qty = 0.0
                position_cost = 0.0
                exited_by_risk = True

        signal = strategy.generate_signal(closes)

        if (not exited_by_risk) and signal == "buy" and position_qty <= 0:
            equity = cash + position_qty * close_price
            notional = _calc_buy_notional(
                cash=cash,
                equity=equity,
                close_price=close_price,
                position_qty=position_qty,
                conf=conf,
            )
            if notional > 10:
                qty = notional / close_price
                fee = notional * fee_rate
                cash -= notional + fee
                position_qty += qty
                position_cost = notional + fee
                trades += 1
                orders.append(
                    {
                        "ts": ts,
                        "side": "buy",
                        "price": close_price,
                        "qty": qty,
                        "fee": fee,
                        "reason": "signal_buy",
                    }
                )

        elif (not exited_by_risk) and signal == "sell" and position_qty > 0:
            notional = position_qty * close_price
            fee = notional * fee_rate
            cash += notional - fee
            pnl = (notional - fee) - position_cost
            if pnl > 0:
                wins += 1
            trades += 1
            orders.append(
                {
                    "ts": ts,
                    "side": "sell",
                    "price": close_price,
                    "qty": position_qty,
                    "fee": fee,
                    "pnl": pnl,
                    "reason": "signal_sell",
                }
            )
            position_qty = 0.0
            position_cost = 0.0

        equity = cash + position_qty * close_price
        max_equity = max(max_equity, equity)
        dd = 0.0 if max_equity <= 0 else (max_equity - equity) / max_equity * 100.0
        max_drawdown_pct = max(max_drawdown_pct, dd)
        equity_curve.append(
            {
                "ts": datetime.utcfromtimestamp(ts / 1000).isoformat(),
                "equity": round(equity, 4),
            }
        )

    final_equity = cash + (position_qty * closes[-1] if closes else 0.0)
    total_pnl = final_equity - initial_capital
    return_pct = 0.0 if initial_capital <= 0 else (total_pnl / initial_capital) * 100.0
    closed_sell_orders = [o for o in orders if o.get("side") == "sell"]
    closed_rounds = max(len(closed_sell_orders), 1)
    win_rate = wins / closed_rounds * 100.0

    total_fees = float(sum(float(o.get("fee") or 0.0) for o in orders))
    realized_pnls = [float(o.get("pnl")) for o in closed_sell_orders if o.get("pnl") is not None]
    gross_profit = float(sum(p for p in realized_pnls if p > 0))
    gross_loss = float(sum(-p for p in realized_pnls if p < 0))
    avg_win = float(gross_profit / max(len([p for p in realized_pnls if p > 0]), 1))
    avg_loss = float(gross_loss / max(len([p for p in realized_pnls if p < 0]), 1))
    avg_trade_pnl = float(sum(realized_pnls) / max(len(realized_pnls), 1))
    expectancy = avg_trade_pnl
    if gross_loss <= 1e-12:
        profit_loss_ratio = None
        profit_factor = None if gross_profit <= 1e-12 else 999.0
    else:
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 1e-12 else None
        profit_factor = gross_profit / gross_loss
    fee_over_notional_pct = 0.0
    turnover = float(sum(float(o.get("price") or 0.0) * float(o.get("qty") or 0.0) for o in orders))
    if turnover > 1e-12:
        fee_over_notional_pct = total_fees / turnover * 100.0
    fee_over_pnl_pct = None
    if abs(total_pnl) > 1e-12:
        fee_over_pnl_pct = total_fees / abs(total_pnl) * 100.0
    diagnostics = {
        "buy_count": len([o for o in orders if o.get("side") == "buy"]),
        "sell_count": len(closed_sell_orders),
        "closed_trades": len(realized_pnls),
        "total_fees": round(total_fees, 6),
        "avg_trade_pnl": round(avg_trade_pnl, 6),
        "avg_win": round(avg_win, 6),
        "avg_loss": round(avg_loss, 6),
        "expectancy_usdt": round(expectancy, 6),
        "profit_loss_ratio": None if profit_loss_ratio is None else round(profit_loss_ratio, 6),
        "profit_factor": None if profit_factor is None else round(profit_factor, 6),
        "gross_profit": round(gross_profit, 6),
        "gross_loss": round(gross_loss, 6),
        "turnover": round(turnover, 6),
        "fee_over_notional_pct": round(fee_over_notional_pct, 6),
        "fee_over_pnl_pct": None if fee_over_pnl_pct is None else round(fee_over_pnl_pct, 6),
    }

    return BacktestResult(
        strategy_name=strategy_name,
        symbol=symbol,
        timeframe=timeframe,
        candles=len(ohlcv),
        trades=trades,
        win_rate=round(win_rate, 2),
        total_pnl=round(total_pnl, 4),
        total_return_pct=round(return_pct, 2),
        max_drawdown_pct=round(max_drawdown_pct, 2),
        initial_capital=round(initial_capital, 4),
        final_equity=round(final_equity, 4),
        equity_curve=equity_curve[:: max(len(equity_curve) // 200, 1)],
        price_curve=price_curve[:: max(len(price_curve) // 400, 1)],
        orders=orders[-300:],
        diagnostics=diagnostics,
    )
