# 🔨 Crypto K线形态监控 Bot

实时监控加密货币 K 线形态，发现目标形态立即推送 Telegram 提醒。

## 🎯 支持检测的形态

| 形态 | 信号 | 说明 |
|------|------|------|
| 🔨 **三连锤** | 强烈看涨反转 | 连续3根：无上引线 + 长下影线（你图中的形态）|
| 🔨 **锤子线** | 潜在反转 | 下跌趋势末端单根锤子线 |
| 📈 **看涨吞没** | 看涨反转 | 绿柱完全包住前根红柱 |
| 📉 **看跌吞没** | 看跌反转 | 红柱完全包住前根绿柱 |

## 🚀 部署到 Railway（推荐）

### 1. 本地准备

```bash
git init
git add .
git commit -m "init crypto kline bot"
git remote add origin https://github.com/你的用户名/crypto-kline-bot.git
git push -u origin main
```

### 2. Railway 部署

1. 登录 [railway.app](https://railway.app)
2. New Project → Deploy from GitHub repo → 选择此仓库
3. 进入 Variables 面板，添加环境变量：

```
TELEGRAM_TOKEN=你的Bot Token
TELEGRAM_CHAT_ID=你的Chat ID
```

4. 部署自动启动 ✅

### 3. 获取 Telegram 配置

- **Bot Token**: 在 Telegram 找 `@BotFather`，发送 `/newbot`
- **Chat ID**: 在 Telegram 找 `@userinfobot`，发送任意消息获取你的 ID

## ⚙️ 自定义监控配置

在 `bot.py` 中修改 `WATCH_LIST`：

```python
WATCH_LIST = [
    {"symbol": "BTCUSDT", "interval": "1h"},
    {"symbol": "ETHUSDT", "interval": "4h"},
    # 添加更多...
]
```

支持的时间级别：`1m` `5m` `15m` `30m` `1h` `4h` `1d`

## 🔧 调整检测灵敏度

在 `bot.py` 的 `PARAMS` 中调整三连锤参数：

```python
"triple_hammer": {
    "min_lower_shadow_ratio": 2.0,  # 下影线 >= 实体 * N 倍，越大越严格
    "max_upper_shadow_ratio": 0.1,  # 上引线 <= 实体 * N，越小越严格
    "min_body_ratio": 0.05,         # 最小实体占比，防止检测到十字星
}
```

## 📱 Telegram 提醒样式

```
🟢 K线信号提醒
━━━━━━━━━━━━━━
📌 形态：🔨 三连锤
🎯 信号：看涨反转
💰 交易对：BTCUSDT
⏱ 时间级别：1小时
💵 当前价格：$67,420.00
🕐 K线时间：06-08 03:00 UTC
━━━━━━━━━━━━━━
📝 连续3根锤子线（无上引线+长下影），强烈看涨反转信号

⚠️ 仅供参考，注意风险管理
```

## 📁 文件结构

```
crypto-kline-bot/
├── bot.py           # 主程序
├── requirements.txt # 依赖
├── railway.toml     # Railway 部署配置
├── .env.example     # 环境变量模板
└── README.md
```
