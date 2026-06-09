"""
Crypto K-Line Short Signal Bot — Kraken Edition
检测严格版「看跌吞没」：
- 前方上升趋势
- 两根柱子顶部平行（高度相近）
- 红柱最高价 > 绿柱最高价
- 红柱实体完全吞没绿柱实体
- 成交量放大
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
    {"symbol": "SOLUSDC",  "pair": "SOLUSDC",  "interval": 60},
    {"symbol": "AVAXUSDC", "pair": "AVAXUSDC", "interval": 60},
    {"symbol": "LINKUSDC", "pair": "LINKUSDC", "interval": 60},
    {"symbol": "NEARUSDC", "pair": "NEARUSDC", "interval": 60},
    {"symbol": "DOTUSDC",  "pair": "DOTUSDC",  "interval": 60},
    {"symbol": "MATICUSDC","pair": "MATICUSDC","interval": 60},
    {"symbol": "ATOMUSDC", "pair": "ATOMUSDC", "interval": 60},
    {"symbol": "UNIUSDC",  "pair": "UNIUSDC",  "interval": 60},
    {"symbol": "FILUSDC",  "pair": "FILUSDC",  "interval": 60},
    {"symbol": "ALGOUSDC", "pair": "ALGOUSDC", "interval": 60},
    {"symbol": "INJUSDC",  "pair": "INJUSDC",  "interval": 60},
    {"symbol": "SUIUSDC",  "pair": "SUIUSDC",  "interval": 60},
    {"symbol": "APTUSDC",  "pair": "APTUSDC",  "interval": 60},
    {"symbol": "OPUSDC",   "pair": "OPUSDC",   "interval": 60},
    {"symbol": "ARBUSDC",  "pair": "ARBUSDC",  "interval": 60},
]

# 止盈止损
TAKE_PROFIT_PCT = 1.3
STOP_LOSS_PCT   = 1.0

# 检测参数
PARAMS = {
    "uptrend_candles":       5,      # 前N根判断上升趋势
    "parallel_top_pct":      0.5,    # 两根柱子顶部高度差 <= 0.5%（平行）
    "volume_surge_ratio":    1.2,    # 成交量 >= 前5根均量 × 1.2
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

# ─── 严格版看跌吞没检测 ───────────────────────────────────────────────────────

def detect_bearish_engulfing(candles):
    """
    严格条件：
    1. 前5根整体上涨（上升趋势）
    2. 前根是阳线（绿柱）
    3. 当根是阴线（红柱）
    4. 两根柱子最高价相近（顶部平行，差距 <= 0.5%）
    5. 红柱最高价 > 绿柱最高价（红柱上影线超过绿柱）
    6. 红柱收盘 < 绿柱开盘（实体完全吞没）
    7. 成交量放大
    """
    if len(candles) < 7:
        return False

    prev = candles[-2]  # 绿柱
    curr = candles[-1]  # 红柱

    # 条件1：上升趋势
    trend = candles[-7:-2]
    if trend[-1]["close"] <= trend[0]["close"]:
        return False

    # 条件2：前根阳线
    if prev["close"] <= prev["open"]:
        return False

    # 条件3：当根阴线
    if curr["close"] >= curr["open"]:
        return False

    # 条件4：顶部平行（两根最高价差距 <= 0.5%）
    high_diff_pct = abs(curr["high"] - prev["high"]) / prev["high"] * 100
    if high_diff_pct > PARAMS["parallel_top_pct"]:
        return False

    # 条件5：红柱最高价 > 绿柱最高价
    if curr["high"] <= prev["high"]:
        return False

    # 条件6：红柱收盘 < 绿柱开盘（实体完全吞没）
    if curr["close"] >= prev["open"]:
        return False

    # 条件7：成交量放大
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
        f"📌 形态：📉 看跌吞没（严格版）\n"
        f"💰 交易对：<b>{symbol}</b>\n"
        f"⏱ 时间级别：{interval_label}\n"
        f"🕐 K线时间：{dt} UTC\n"
        f"━━━━━━━━━━━━━━\n"
        f"💵 入场价：<code>${price:,.4f}</code>\n"
        f"🎯 止盈价：<code>${tp_price:,.4f}</code>  (-{TAKE_PROFIT_PCT}%)\n"
        f"🛑 止损价：<code>${sl_price:,.4f}</code>  (+{STOP_LOSS_PCT}%)\n"
        f"━━━━━━━━━━━━━━\n"
        f"✅ 上升趋势顶部\n"
        f"✅ 顶部平行（阻力位）\n"
        f"✅ 红柱突破绿柱高点\n"
        f"✅ 成交量放大确认\n"
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
    log.info("🚀 做空信号 Bot 启动（严格版）")
    pairs_str = ", ".join(w["symbol"] for w in WATCH_LIST)

    async with aiohttp.ClientSession() as session:
        await send_telegram(session,
            f"🤖 <b>做空信号 Bot 已启动（严格版）</b>\n"
            f"📌 形态：看跌吞没\n"
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
                            log.info(f"🔴 做空信号: {watch['symbol']} @ ${price:,.4f}")
                            await send_telegram(session, format_alert(watch["symbol"], interval_label, price, dt))

                except Exception as e:
                    log.error(f"处理 {watch['pair']} 出错: {e}")

                await asyncio.sleep(2)

            log.info(f"扫描完成，{POLL_INTERVAL}秒后下次扫描...")
            await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    asyncio.run(run())
