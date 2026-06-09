"""
Crypto K-Line Pattern Monitor Bot — Kraken Edition
监测 Kraken K 线形态（1小时），发现目标形态后通过 Telegram 推送提醒
核心形态：三连锤（连续3根锤子线：上方无引线/极短，下方长下影线）
"""

import os
import asyncio
import logging
import time
from datetime import datetime
import aiohttp
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

# ─── 配置 ────────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Kraken 交易对命名规则：XBT=BTC, 用 "XBT/USD" 格式
# interval 单位：分钟  60=1h  240=4h  1440=日线
WATCH_LIST = [
    {"symbol": "XBTUSD",  "pair": "XBT/USD",  "interval": 60},
    {"symbol": "ETHUSD",  "pair": "ETH/USD",  "interval": 60},
    {"symbol": "SOLUSD",  "pair": "SOL/USD",  "interval": 60},
    {"symbol": "XBTUSD",  "pair": "XBT/USD",  "interval": 240},
    {"symbol": "ETHUSD",  "pair": "ETH/USD",  "interval": 240},
]

# 检测参数（可调整）
PARAMS = {
    # 三连锤 — 你图中圈出的形态
    "triple_hammer": {
        "enabled": True,
        "min_lower_shadow_ratio": 2.0,  # 下影线 >= 实体 × 2
        "max_upper_to_body":      0.2,  # 上引线 <= 实体 × 20%（几乎无上引线）
        "min_body_ratio":         0.05, # 实体占整根K线 >= 5%（排除十字星）
    },
    # 单根锤子线（下跌趋势末端）
    "single_hammer": {
        "enabled": True,
        "min_lower_shadow_ratio": 2.5,
        "max_upper_to_body":      0.2,
        "min_body_ratio":         0.05,
        "require_downtrend":      True,
    },
    # 看涨吞没
    "bullish_engulfing": {"enabled": True},
    # 看跌吞没
    "bearish_engulfing": {"enabled": True},
}

# 轮询间隔（秒）— 1小时K线每5分钟扫一次足够
POLL_INTERVAL = 300

# ─── Kraken K 线获取 ──────────────────────────────────────────────────────────

KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"

async def fetch_klines(session: aiohttp.ClientSession, watch: dict, limit: int = 20):
    """
    从 Kraken 拉取 OHLC 数据
    Kraken 返回格式：[time, open, high, low, close, vwap, volume, count]
    """
    params = {
        "pair":     watch["pair"],
        "interval": watch["interval"],   # 分钟数
    }
    try:
        async with session.get(
            KRAKEN_OHLC_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            data = await resp.json()

            if data.get("error"):
                log.error(f"Kraken API 错误 {watch['pair']}: {data['error']}")
                return []

            # result key 是 pair 名称（可能带变体，如 XXBTZUSD）
            result_key = list(data["result"].keys())[0]
            rows = data["result"][result_key]

            # 取最新 limit 根（Kraken 默认返回720根，最后一根是未完成的当前K线，去掉）
            rows = rows[-(limit + 1): -1]

            candles = []
            for r in rows:
                candles.append({
                    "open_time": int(r[0]),
                    "open":   float(r[1]),
                    "high":   float(r[2]),
                    "low":    float(r[3]),
                    "close":  float(r[4]),
                    "volume": float(r[6]),
                })
            return candles

    except Exception as e:
        log.error(f"获取 {watch['pair']} {watch['interval']}min K线失败: {e}")
        return []

# ─── K 线形态判断 ─────────────────────────────────────────────────────────────

def candle_parts(c):
    body         = abs(c["close"] - c["open"])
    total        = c["high"] - c["low"]
    if total == 0:
        return None
    upper_shadow = c["high"] - max(c["open"], c["close"])
    lower_shadow = min(c["open"], c["close"]) - c["low"]
    return {
        "body":          body,
        "total":         total,
        "upper_shadow":  upper_shadow,
        "lower_shadow":  lower_shadow,
        "body_ratio":    body / total,
        "upper_to_body": upper_shadow / body if body > 0 else 999,
        "lower_to_body": lower_shadow / body if body > 0 else 0,
        "is_bullish":    c["close"] >= c["open"],
    }

def is_hammer_candle(c, cfg):
    """
    锤子线条件：
    ① 实体占比 >= min_body_ratio（排除十字星）
    ② 下影线 >= 实体 × min_lower_shadow_ratio
    ③ 上引线 <= 实体 × max_upper_to_body
    """
    p = candle_parts(c)
    if p is None:
        return False
    if p["body_ratio"] < cfg["min_body_ratio"]:
        return False
    if p["lower_to_body"] < cfg["min_lower_shadow_ratio"]:
        return False
    if p["upper_to_body"] > cfg["max_upper_to_body"]:
        return False
    return True

def detect_triple_hammer(candles, cfg):
    """连续3根都是锤子线"""
    if len(candles) < 3:
        return False
    return all(is_hammer_candle(c, cfg) for c in candles[-3:])

def detect_single_hammer(candles, cfg):
    """最新一根是锤子线，且前5根整体下跌"""
    if len(candles) < 6:
        return False
    if not is_hammer_candle(candles[-1], cfg):
        return False
    if cfg.get("require_downtrend"):
        prev = candles[-6:-1]
        return prev[-1]["close"] < prev[0]["close"]
    return True

def detect_bullish_engulfing(candles):
    if len(candles) < 2:
        return False
    prev, curr = candles[-2], candles[-1]
    return (
        prev["close"] < prev["open"] and          # 前根阴线
        curr["close"] > curr["open"] and          # 当根阳线
        curr["open"]  <= prev["close"] and        # 开盘低于前收
        curr["close"] >= prev["open"]             # 收盘高于前开
    )

def detect_bearish_engulfing(candles):
    if len(candles) < 2:
        return False
    prev, curr = candles[-2], candles[-1]
    return (
        prev["close"] > prev["open"] and
        curr["close"] < curr["open"] and
        curr["open"]  >= prev["close"] and
        curr["close"] <= prev["open"]
    )

# ─── 形态检测入口 ─────────────────────────────────────────────────────────────

def detect_patterns(candles, watch):
    alerts = []
    latest    = candles[-1]
    price     = latest["close"]
    symbol    = watch["symbol"]
    interval  = watch["interval"]
    dt = datetime.utcfromtimestamp(latest["open_time"]).strftime("%m-%d %H:%M")

    interval_label = {60: "1小时", 240: "4小时", 1440: "日线"}.get(interval, f"{interval}min")

    base = {"symbol": symbol, "interval": interval_label, "price": price, "time": dt}

    if PARAMS["triple_hammer"]["enabled"] and detect_triple_hammer(candles, PARAMS["triple_hammer"]):
        alerts.append({**base,
            "pattern": "🔨 三连锤",
            "signal":  "强烈看涨反转",
            "emoji":   "🟢",
            "desc":    "连续3根锤子线（无上引线＋长下影），是强烈的底部反转信号",
        })

    if PARAMS["single_hammer"]["enabled"] and detect_single_hammer(candles, PARAMS["single_hammer"]):
        alerts.append({**base,
            "pattern": "🔨 锤子线",
            "signal":  "潜在反转",
            "emoji":   "🟡",
            "desc":    "下跌趋势末端出现锤子线，注意可能反转",
        })

    if PARAMS["bullish_engulfing"]["enabled"] and detect_bullish_engulfing(candles):
        alerts.append({**base,
            "pattern": "📈 看涨吞没",
            "signal":  "看涨反转",
            "emoji":   "🟢",
            "desc":    "阳线完全吞没前根阴线，买入信号",
        })

    if PARAMS["bearish_engulfing"]["enabled"] and detect_bearish_engulfing(candles):
        alerts.append({**base,
            "pattern": "📉 看跌吞没",
            "signal":  "看跌反转",
            "emoji":   "🔴",
            "desc":    "阴线完全吞没前根阳线，注意下行风险",
        })

    return alerts

# ─── Telegram 推送 ────────────────────────────────────────────────────────────

async def send_telegram(session: aiohttp.ClientSession, message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            result = await resp.json()
            if not result.get("ok"):
                log.error(f"Telegram 发送失败: {result}")
            else:
                log.info("✅ Telegram 消息已发送")
    except Exception as e:
        log.error(f"Telegram 异常: {e}")

def format_alert(alert):
    return (
        f"{alert['emoji']} <b>Kraken K线信号</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"📌 形态：{alert['pattern']}\n"
        f"🎯 信号：{alert['signal']}\n"
        f"💰 交易对：<b>{alert['symbol']}</b>\n"
        f"⏱ 时间级别：{alert['interval']}\n"
        f"💵 当前价格：<code>${alert['price']:,.2f}</code>\n"
        f"🕐 K线时间：{alert['time']} UTC\n"
        f"━━━━━━━━━━━━━━\n"
        f"📝 {alert['desc']}\n"
        f"\n⚠️ 仅供参考，注意风险管理"
    )

# ─── 去重（同信号冷却60分钟）────────────────────────────────────────────────────

class AlertDedup:
    def __init__(self, cooldown_min=60):
        self._seen = {}
        self._cooldown = cooldown_min * 60

    def should_send(self, alert):
        key = f"{alert['symbol']}_{alert['interval']}_{alert['pattern']}"
        now = time.time()
        if key in self._seen and now - self._seen[key] < self._cooldown:
            return False
        self._seen[key] = now
        return True

dedup = AlertDedup(cooldown_min=60)

# ─── 主循环 ───────────────────────────────────────────────────────────────────

async def run():
    log.info("🚀 Kraken K线监控 Bot 启动")
    pairs_str = ", ".join(f"{w['pair']}({w['interval']}min)" for w in WATCH_LIST)
    log.info(f"监控列表: {pairs_str}")

    async with aiohttp.ClientSession() as session:
        await send_telegram(session,
            f"🤖 <b>Kraken K线监控 Bot 已启动</b>\n"
            f"监控中: {pairs_str}\n"
            f"扫描间隔: 每{POLL_INTERVAL//60}分钟"
        )

        while True:
            log.info("--- 开始新一轮扫描 ---")
            for watch in WATCH_LIST:
                try:
                    candles = await fetch_klines(session, watch, limit=20)
                    if not candles:
                        continue

                    alerts = detect_patterns(candles, watch)
                    for alert in alerts:
                        if dedup.should_send(alert):
                            log.info(f"🔔 触发: {alert['pattern']} @ {alert['symbol']} {alert['interval']}")
                            await send_telegram(session, format_alert(alert))
                        else:
                            log.debug(f"去重跳过: {alert['pattern']} @ {alert['symbol']}")

                except Exception as e:
                    log.error(f"处理 {watch['pair']} 时出错: {e}")

                await asyncio.sleep(2)  # Kraken 限速友好间隔

            log.info(f"扫描完成，{POLL_INTERVAL}秒后下次扫描...")
            await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    asyncio.run(run())
