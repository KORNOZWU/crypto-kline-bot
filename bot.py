"""
Crypto K-Line Short Signal Bot — Kraken Edition
只检测「看跌吞没」形态，推送做空信号到 Telegram
条件：上升趋势顶部 + 阴线完全吞没前根阳线 + 成交量放大
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

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

WATCH_LIST = [
    {"symbol": "XBTUSD",  "pair": "XBTUSD",  "interval": 60},
    {"symbol": "ETHUSD",  "pair": "ETHUSD",  "interval": 60},
    {"symbol": "SOLUSD",  "pair": "SOLUSD",  "interval": 60},
    {"symbol": "XRPUSD",  "pair": "XRPUSD",  "interval": 60},
    {"symbol": "ADAUSD",  "pair": "ADAUSD",  "interval": 60},
    {"symbol": "DOTUSD",  "pair": "DOTUSD",  "interval": 60},
    {"symbol": "LINKUSD", "pair": "LINKUSD", "interval": 60},
    {"symbol": "AVAXUSD", "pair": "AVAXUSD", "interval": 60},
    {"symbol": "LTCUSD",  "pair": "LTCUSD",  "interval": 60},
    {"symbol": "UNIUSD",  "pair": "UNIUSD",  "interval": 60},
    {"symbol": "MATICUSD","pair": "MATICUSD","interval": 60},
    {"symbol": "ATOMUSD", "pair": "ATOMUSD", "interval": 60},
    {"symbol": "FILUSD",  "pair": "FILUSD",  "interval": 60},
    {"symbol": "NEARUSD", "pair": "NEARUSD", "interval": 60},
    {"symbol": "ALGOUSD", "pair": "ALGOUSD", "interval": 60},
]

# 止盈止损设置
TAKE_PROFIT_PCT = 1.3   # 止盈 1.3%
STOP_LOSS_PCT   = 1.0   # 止损 1.0%

# 检测参数
PARAMS = {
    "require_uptrend":      True,
    "require_volume_surge": True,
    "volume_surge_ratio":   1.2,
    "uptrend_candles":      5,
}

POLL_INTERVAL = 120

# ─── Kraken K 线获取 ──────────────────────────────────────────────────────────

KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"

async def fetch_klines(session, watch, limit=20):
    params = {"pair": watch["pair"], "interval": watch["interval"]}
    try:
        async with session.get(
            KRAKEN_OHLC_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            data = await resp.json()
            if data.get("error"):
                log.error(f"Kraken错误 {watch['pair']}: {data['error']}")
                return []
            result_key = list(data["result"].keys())[0]
            rows = data["result"][result_key]
            rows = rows[-(limit + 1):-1]
            return [{
                "open_time": int(r[0]),
                "open":   float(r[1]),
                "high":   float(r[2]),
                "low":    float(r[3]),
                "close":  float(r[4]),
                "volume": float(r[6]),
            } for r in rows]
    except Exception as e:
        log.error(f"获取 {watch['pair']} K线失败: {e}")
        return []

# ─── 看跌吞没检测 ─────────────────────────────────────────────────────────────

def detect_bearish_engulfing(candles):
    if len(candles) < 7:
        return False

    prev = candles[-2]
    curr = candles[-1]

    # 前根阳线
    if prev["close"] <= prev["open"]:
        return False
    # 当根阴线
    if curr["close"] >= curr["open"]:
        return False
    # 完全吞没
    if not (curr["open"] >= prev["close"] and curr["close"] <= prev["open"]):
        return False
    # 上升趋势
    if PARAMS["require_uptrend"]:
        trend = candles[-7:-2]
        if trend[-1]["close"] <= trend[0]["close"]:
            return False
    # 成交量放大
    if PARAMS["require_volume_surge"]:
        avg_vol = sum(c["volume"] for c in candles[-7:-2]) / 5
        if avg_vol > 0 and curr["volume"] < avg_vol * PARAMS["volume_surge_ratio"]:
            return False

    return True

# ─── Telegram 推送 ────────────────────────────────────────────────────────────

async def send_telegram(session, message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            result = await resp.json()
            if not result.get("ok"):
                log.error(f"Telegram失败: {result}")
            else:
                log.info("✅ Telegram已发送")
    except Exception as e:
        log.error(f"Telegram异常: {e}")

def format_alert(symbol, interval_label, price, dt):
    tp_price = price * (1 - TAKE_PROFIT_PCT / 100)
    sl_price = price * (1 + STOP_LOSS_PCT   / 100)
    return (
        f"🔴 <b>做空信号 — Kraken</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"📌 形态：📉 看跌吞没\n"
        f"💰 交易对：<b>{symbol}</b>\n"
        f"⏱ 时间级别：{interval_label}\n"
        f"🕐 K线时间：{dt} UTC\n"
        f"━━━━━━━━━━━━━━\n"
        f"💵 入场价：<code>${price:,.2f}</code>\n"
        f"🎯 止盈价：<code>${tp_price:,.2f}</code>  (-{TAKE_PROFIT_PCT}%)\n"
        f"🛑 止损价：<code>${sl_price:,.2f}</code>  (+{STOP_LOSS_PCT}%)\n"
        f"━━━━━━━━━━━━━━\n"
        f"📝 上升趋势顶部看跌吞没\n"
        f"成交量放大确认，做空概率 ~70%\n"
        f"\n⚠️ 仅供参考，注意风险管理"
    )

# ─── 去重 ────────────────────────────────────────────────────────────────────

class AlertDedup:
    def __init__(self, cooldown_min=60):
        self._seen = {}
        self._cooldown = cooldown_min * 60

    def should_send(self, key):
        now = time.time()
        if key in self._seen and now - self._seen[key] < self._cooldown:
            return False
        self._seen[key] = now
        return True

dedup = AlertDedup(cooldown_min=60)

# ─── 主循环 ───────────────────────────────────────────────────────────────────

async def run():
    log.info("🚀 做空信号 Bot 启动")
    pairs_str = ", ".join(w["symbol"] for w in WATCH_LIST)

    async with aiohttp.ClientSession() as session:
        await send_telegram(session,
            f"🤖 <b>做空信号 Bot 已启动</b>\n"
            f"📌 监测形态：看跌吞没\n"
            f"🎯 止盈：{TAKE_PROFIT_PCT}%  🛑 止损：{STOP_LOSS_PCT}%\n"
            f"监控: {pairs_str}\n"
            f"扫描间隔: 每{POLL_INTERVAL//60}分钟"
        )

        while True:
            log.info("--- 开始扫描 ---")
            for watch in WATCH_LIST:
                try:
                    candles = await fetch_klines(session, watch, limit=20)
                    if not candles:
                        continue

                    if detect_bearish_engulfing(candles):
                        latest = candles[-1]
                        price  = latest["close"]
                        dt     = datetime.utcfromtimestamp(latest["open_time"]).strftime("%m-%d %H:%M")
                        interval_label = {60: "1小时", 240: "4小时"}.get(watch["interval"], f"{watch['interval']}min")
                        key    = f"{watch['symbol']}_{watch['interval']}"

                        if dedup.should_send(key):
                            log.info(f"🔴 做空信号: {watch['symbol']} @ ${price:,.2f}")
                            await send_telegram(session, format_alert(watch["symbol"], interval_label, price, dt))

                except Exception as e:
                    log.error(f"处理 {watch['pair']} 出错: {e}")

                await asyncio.sleep(2)

            log.info(f"扫描完成，{POLL_INTERVAL}秒后下次扫描...")
            await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    asyncio.run(run())
