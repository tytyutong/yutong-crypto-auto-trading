# 全自动数字货币量化交易（Binance）

当前版本是可运行的全栈 MVP，已包含：
- 多策略切换：`ema_cross`、`bollinger`、`rsi`、`grid`
- 回测模块：历史 K 线回放 + 绩效报表
- 风控告警：飞书 Webhook 推送
- 守护部署：Docker Compose 一键启动 + 自动重启

## 1. 本地运行

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python run.py
```

打开：
- [http://127.0.0.1:8000](http://127.0.0.1:8000)

## 2. 核心配置（.env）

```env
APP_HOST=0.0.0.0
APP_PORT=8000
STORAGE_DB_PATH=data/trade.db

TRADE_MODE=paper
LIVE_CONFIRM=false
USE_BINANCE_TESTNET=false
BINANCE_API_KEY=
BINANCE_API_SECRET=

DEFAULT_SYMBOL=BTC/USDT
TIMEFRAME=1m
POLL_SECONDS=20

STRATEGY_NAME=ema_cross
EMA_FAST=9
EMA_SLOW=21
BOLL_WINDOW=20
BOLL_STD_MULT=2.0
RSI_PERIOD=14
RSI_OVERBOUGHT=70
RSI_OVERSOLD=30
GRID_PCT=0.01

RISK_MAX_POSITION_USDT=100
RISK_MAX_DAILY_LOSS_USDT=50

FEISHU_ALERT_ENABLED=false
FEISHU_WEBHOOK_URL=
```

## 3. 策略切换

可在前端页面选择策略并传参数 JSON，例如：
- `ema_cross`: `{"fast":9,"slow":21}`
- `bollinger`: `{"window":20,"std_mult":2.0}`
- `rsi`: `{"period":14,"overbought":70,"oversold":30}`
- `grid`: `{"grid_pct":0.01}`

也可通过 API：

```bash
curl -X POST http://127.0.0.1:8000/api/start \
  -H "Content-Type: application/json" \
  -d "{\"symbol\":\"BTC/USDT\",\"strategy_name\":\"rsi\",\"strategy_params\":{\"period\":14,\"overbought\":70,\"oversold\":30}}"
```

## 4. 回测接口

```bash
curl -X POST http://127.0.0.1:8000/api/backtest \
  -H "Content-Type: application/json" \
  -d "{\"symbol\":\"BTC/USDT\",\"timeframe\":\"1m\",\"strategy_name\":\"bollinger\",\"strategy_params\":{\"window\":20,\"std_mult\":2.0},\"limit\":1000,\"initial_capital\":10000,\"order_size_usdt\":100,\"fee_rate\":0.001}"
```

返回结果包含：
- `total_pnl`
- `total_return_pct`
- `max_drawdown_pct`
- `win_rate`
- `trades`
- `final_equity`
- `equity_curve`

## 5. 飞书风控告警

在飞书群机器人中获取 Webhook URL，配置：

```env
FEISHU_ALERT_ENABLED=true
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxx
```

触发场景：
- 引擎启动/停止
- 风控拦截（仓位超限、当日亏损超限）
- 引擎异常

## 6. Docker 一键部署（守护）

```bash
copy .env.example .env
docker compose up -d --build
```

停止：

```bash
docker compose down
```

守护策略：
- `docker-compose.yml` 已配置 `restart: unless-stopped`
- 数据库保存在命名卷 `quant_data`（挂载到容器 `/app/data`）

## 7. 安全提示（实盘前必看）

1. 先使用 `TRADE_MODE=paper` 验证策略。
2. 实盘必须同时设置：`TRADE_MODE=live` 与 `LIVE_CONFIRM=true`。
3. 强烈建议先在 Binance Testnet 做联调，再小资金实盘。
