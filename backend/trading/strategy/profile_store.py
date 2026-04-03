from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.trading.strategy.registry import list_strategies


class StrategyProfileStore:
    def __init__(self, path: str = "data/strategy_profiles.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.path.exists():
            self._write_all(self._default_profiles())

    def _default_params(self, strategy_name: str) -> dict[str, Any]:
        if strategy_name == "ema_cross":
            return {"fast": 9, "slow": 21}
        if strategy_name == "bollinger":
            return {"window": 20, "std_mult": 2.0}
        if strategy_name == "rsi":
            return {"period": 14, "overbought": 70, "oversold": 30}
        if strategy_name == "grid":
            return {"grid_pct": 0.01}
        if strategy_name == "hf_scalp":
            return {"lookback": 4, "entry_bps": 3.0, "exit_bps": 2.0}
        if strategy_name == "smart_adaptive":
            return {
                "timeframe": "10m",
                "signal_kline_limit": 300,
                "fast": 12,
                "slow": 48,
                "trend_threshold_pct": 0.18,
                "trend_exit_threshold_pct": 0.05,
                "rsi_period": 14,
                "trend_pullback_rsi": 46,
                "trend_takeprofit_rsi": 69,
                "bb_window": 20,
                "trend_pullback_z": 0.35,
                "trend_takeprofit_z": 1.25,
                "range_entry_z": 1.15,
                "range_exit_z": 0.75,
                "range_buy_rsi": 43,
                "range_sell_rsi": 58,
                "vol_window": 20,
                "max_vol_pct": 2.8,
                "cooldown_bars": 2,
            }
        return {}

    def _smart_adaptive_optimized_params(self) -> dict[str, Any]:
        # Fee-aware low-frequency profile:
        # reduce overtrading in chop while keeping trend entries.
        return {
            "timeframe": "30m",
            "signal_kline_limit": 800,
            "fast": 16,
            "slow": 72,
            "trend_threshold_pct": 0.28,
            "trend_exit_threshold_pct": 0.08,
            "rsi_period": 14,
            "trend_pullback_rsi": 42,
            "trend_takeprofit_rsi": 74,
            "bb_window": 20,
            "trend_pullback_z": 0.6,
            "trend_takeprofit_z": 1.6,
            "range_entry_z": 1.5,
            "range_exit_z": 1.1,
            "range_buy_rsi": 36,
            "range_sell_rsi": 64,
            "vol_window": 20,
            "max_vol_pct": 2.2,
            "cooldown_bars": 6,
        }

    def _default_profiles(self) -> list[dict[str, Any]]:
        now = datetime.utcnow().isoformat()
        out = []
        for name in list_strategies():
            out.append(
                {
                    "id": str(uuid.uuid4()),
                    "name": f"{name}_default",
                    "strategy_name": name,
                    "params": self._default_params(name),
                    "enabled": True,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        return out

    def _read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_text(encoding="utf-8").strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        return [x for x in data if isinstance(x, dict)]

    def _write_all(self, rows: list[dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    def list(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._read_all()
            if not rows:
                rows = self._default_profiles()
                self._write_all(rows)
            else:
                changed = False
                existing_by_name = {str(x.get("name", "")).strip().lower() for x in rows}
                now = datetime.utcnow().isoformat()
                # keep default profiles up-to-date when new default params are introduced
                for row in rows:
                    row_name = str(row.get("name", "")).strip().lower()
                    strategy_name = str(row.get("strategy_name", "")).strip().lower()
                    if not row_name.endswith("_default"):
                        continue
                    if not strategy_name:
                        continue
                    default_params = self._default_params(strategy_name)
                    if not default_params:
                        continue
                    params = row.get("params")
                    if not isinstance(params, dict):
                        params = {}
                    missing = False
                    for k, v in default_params.items():
                        if k not in params:
                            params[k] = v
                            missing = True
                    if missing:
                        row["params"] = params
                        row["updated_at"] = now
                        changed = True
                for name in list_strategies():
                    default_name = f"{name}_default"
                    if default_name.lower() in existing_by_name:
                        continue
                    rows.append(
                        {
                            "id": str(uuid.uuid4()),
                            "name": default_name,
                            "strategy_name": name,
                            "params": self._default_params(name),
                            "enabled": True,
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
                    changed = True

                # Ensure an out-of-box optimized profile exists for smart_adaptive.
                optimized_name = "smart_adaptive_optimized"
                if optimized_name.lower() not in existing_by_name:
                    rows.append(
                        {
                            "id": str(uuid.uuid4()),
                            "name": optimized_name,
                            "strategy_name": "smart_adaptive",
                            "params": self._smart_adaptive_optimized_params(),
                            "enabled": True,
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
                    changed = True
                if changed:
                    self._write_all(rows)
            if enabled_only:
                rows = [x for x in rows if bool(x.get("enabled"))]
            rows.sort(key=lambda x: x.get("name", ""))
            return rows

    def get(self, profile_id: str) -> dict[str, Any]:
        rows = self.list(enabled_only=False)
        for row in rows:
            if row.get("id") == profile_id:
                return row
        raise ValueError("profile not found")

    def create(
        self,
        *,
        name: str,
        strategy_name: str,
        params: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        strategy_name = strategy_name.strip().lower()
        if strategy_name not in list_strategies():
            raise ValueError("unknown strategy_name")
        if not name.strip():
            raise ValueError("name is required")

        with self._lock:
            rows = self._read_all()
            if any(str(x.get("name", "")).strip().lower() == name.strip().lower() for x in rows):
                raise ValueError("profile name already exists")
            now = datetime.utcnow().isoformat()
            row = {
                "id": str(uuid.uuid4()),
                "name": name.strip(),
                "strategy_name": strategy_name,
                "params": params or {},
                "enabled": bool(enabled),
                "created_at": now,
                "updated_at": now,
            }
            rows.append(row)
            self._write_all(rows)
            return row

    def update(
        self,
        profile_id: str,
        *,
        name: str | None = None,
        strategy_name: str | None = None,
        params: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            rows = self._read_all()
            target = None
            for row in rows:
                if row.get("id") == profile_id:
                    target = row
                    break
            if target is None:
                raise ValueError("profile not found")

            if name is not None:
                n = name.strip()
                if not n:
                    raise ValueError("name is required")
                if any(
                    str(x.get("name", "")).strip().lower() == n.lower() and x.get("id") != profile_id
                    for x in rows
                ):
                    raise ValueError("profile name already exists")
                target["name"] = n

            if strategy_name is not None:
                key = strategy_name.strip().lower()
                if key not in list_strategies():
                    raise ValueError("unknown strategy_name")
                target["strategy_name"] = key

            if params is not None:
                target["params"] = params

            if enabled is not None:
                target["enabled"] = bool(enabled)

            target["updated_at"] = datetime.utcnow().isoformat()
            self._write_all(rows)
            return target

    def delete(self, profile_id: str) -> None:
        with self._lock:
            rows = self._read_all()
            next_rows = [x for x in rows if x.get("id") != profile_id]
            if len(next_rows) == len(rows):
                raise ValueError("profile not found")
            self._write_all(next_rows)
