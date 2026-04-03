from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_host: str = "0.0.0.0"
    app_port: int = 8000
    storage_db_path: str = "trade.db"
    exchange_name: str = "binance"

    trade_mode: str = "paper"
    live_confirm: bool = False
    use_binance_testnet: bool = False

    binance_api_key: str = ""
    binance_api_secret: str = ""

    default_symbol: str = "BTC/USDT"
    timeframe: str = "1m"
    poll_seconds: int = 20

    strategy_name: str = "ema_cross"
    ema_fast: int = 9
    ema_slow: int = 21
    boll_window: int = 20
    boll_std_mult: float = 2.0
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    grid_pct: float = 0.01

    risk_max_position_usdt: float = 100.0
    risk_max_daily_loss_usdt: float = 50.0
    paper_equity_usdt: float = 10000.0

    feishu_webhook_url: str = ""
    feishu_alert_enabled: bool = False


settings = Settings()
