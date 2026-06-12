"""
Crypto K-Line Short Signal Bot — Kraken Edition
看跌吞没做空信号（严格版）
条件4：实体顶部平行（差距 <= 1.5%）
条件5：红柱引线严格高于绿柱引线
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

TAKE_PROFIT_PCT = 1.5
STOP_LOSS_PCT   = 1.0

PARAMS = {
    "parallel_body_top_pct": 1.5,  # 实体顶部差距 <= 1.5%
}

POLL_INTERVAL = 120

# ─── 自动获取 Kraken 杠杆 USD 交易对 ─────────────────────────────────────────

KRAKEN_ASSET_PAIRS_URL = "https://api.kraken.com/0/public/AssetPairs"
KRAKEN_OHLC_URL        = "https://api.kraken.com/0/public/OHLC"

async def fetch_margin_usd_pairs(session):
    KNOWN_PAIRS = [
        {"symbol": "BTCUSD",  "pair": "XBT/USD",  "interval": 60, "max_lev": 10},
        {"symbol": "ETHUSD",  "pair": "ETH/USD",  "interval": 60, "max_lev": 10},
        {"symbol": "SOLUSD",  "pair": "SOL/USD",  "interval": 60, "max_lev": 10},
        {"symbol": "XRPUSD",  "pair": "XRP/USD",  "interval": 60, "max_lev": 10},
        {"symbol": "ADAUSD",  "pair": "ADA/USD",  "interval": 60, "max_lev": 10},
        {"symbol": "DOGEUSD", "pair": "DOGE/USD", "interval": 60, "max_lev": 10},
        {"symbol": "LTCUSD",  "pair": "LTC/USD",  "interval": 60, "max_lev": 10},
        {"symbol": "LINKUSD", "pair": "LINK/USD", "interval": 60, "max_lev": 10},
        {"symbol": "AVAXUSD", "pair": "AVAX/USD", "interval": 60, "max_lev": 10},
        {"symbol": "SUIUSD",  "pair": "SUI/USD",  "interval": 60, "max_lev": 10},
        {"symbol": "HYPEUSD", "pair": "HYPE/USD", "interval": 60, "max_lev": 5},
        {"symbol": "ZECUSD",  "pair": "ZEC/USD",  "interval": 60, "max_lev": 5},
        {"symbol": "NEARUSD", "pair": "NEAR/USD", "interval": 60, "max_lev": 3},
        {"symbol": "XLMUSD",  "pair": "XLM/USD",  "interval": 60, "max_lev": 2},
        {"symbol": "DOTUSD",  "pair": "DOT/USD",  "interval": 60, "max_lev": 5},
        {"symbol": "ALGOUSD", "pair": "ALGO/USD", "interval": 60, "max_lev": 2},
        {"symbol": "BCHUSD",  "pair": "BCH/USD",  "interval": 60, "max_lev": 5},
        {"symbol": "UNIUSD",  "pair": "UNI/USD",  "interval": 60, "max_lev": 5},
        {"symbol": "SHIBUSD", "pair": "SHIB/USD", "interval": 60, "max_lev": 5},
        {"symbol": "AAVEUSD", "pair": "AAVE/USD", "interval": 60, "max_lev": 5},
        {"symbol": "TRXUSD",  "pair": "TRX/USD",  "interval": 60, "max_lev": 5},
        {"symbol": "CRVUSD",  "pair": "CRV/USD",  "interval": 60, "max_lev": 5},
        {"symbol": "HBARUSD", "pair": "HBAR/USD", "interval": 60, "max_lev": 5},
    ]
    known_symbols = {p["symbol"] for p in KNOWN_PAIRS}
    try:
        async with session.get(
            KRAKEN_ASSET_PAIRS_URL,
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            data = await resp.json()
            if not data.get("error"):
                for pair_name, info in data["result"].items():
                    quote = info.get("quote", "")
                    if quote not in ("ZUSD", "USD"):
                        continue
                    leverage_buy = info.get("leverage_buy", [])
                    if not leverage_buy:
                        continue
                    if pair_name.endswith(".d"):
                        continue
                    base = info.get("base", "")
                    stables = {"USDT", "USDC", "DAI", "ZUSD", "PAXG", "XAUT"}
                    if base in stables:
                        continue
                    altname = info.get("altname", pair_name)
                    symbol = altname.replace("/", "").replace("XBT", "BTC")
                    if symbol not in known_symbols:
                        KNOWN_PAIRS.append({
                            "symbol":   symbol,
                            "pair":     altname,
                            "interval": 60,
                            "max_lev":  max(leverage_buy),
                        })
                        log.info(f"新增交易对: {symbol}")
    except Exception as e:
        log.warning(f"API检查新交易对失败: {e}")
    log.info(f"✅ 共 {len(KNOWN_PAIRS)} 个杠杆 USD 交易对")
    return KNOWN_PAIRS


# ─── K 线获取 ─────────────────────────────────────────────────────────────────

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
    except Exception:
        return []

# ─── 看跌吞没检测 ─────────────────────────────────────────────────────────────

def detect_bearish_engulfing(candles):
    """
    条件1：前3根结构 — c1(红或绿) + c2(绿) + c3(绿↑)
    条件2：prev 是绿柱
    条件3：curr 是红柱，实体完全吞没 prev 实体
    条件4：实体顶部平行 — max(curr.open,curr.close) vs max(prev.open,prev.close) 差距 ≤ 1.5%
    条件5：红柱引线严格高于绿柱引线 — curr.high > prev.high
    条件6：成交量放大
    """
    if len(candles) < 8:
        return False

    prev = candles[-2]
    curr = candles[-1]
    c1   = candles[-5]
    c2   = candles[-4]
    c3   = candles[-3]

    # 条件2：prev 阳线
    if prev["close"] <= prev["open"]:
        return False

    # 条件3：curr 阴线
    if curr["close"] >= curr["open"]:
        return False

    # 条件1：c2 必须绿柱
    if c2["close"] <= c2["open"]:
        return False

    # 条件1：c3 必须绿柱且收盘 > c2
    if c3["close"] <= c3["open"]:
        return False
    if c3["close"] <= c2["close"]:
        return False

    # 条件3：红柱实体完全吞没绿柱实体
    if curr["close"] >= prev["open"]:
        return False

    # 条件4：实体顶部平行（用实体最高点比较）
    curr_body_top = max(curr["open"], curr["close"])
    prev_body_top = max(prev["open"], prev["close"])
    body_top_diff_pct = abs(curr_body_top - prev_body_top) / prev_body_top * 100
    if body_top_diff_pct > PARAMS["parallel_body_top_pct"]:
        return False

    # 条件5：红柱引线（最高价）严格大于绿柱引线（最高价）
    if curr["high"] <= prev["high"]:
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

def format_alert(symbol, interval_label, price, dt, max_lev, open_time):
    tp_price   = price * (1 - TAKE_PROFIT_PCT / 100)
    sl_price   = price * (1 + STOP_LOSS_PCT   / 100)
    close_time = datetime.utcfromtimestamp(open_time + 4 * 3600).strftime("%m-%d %H:%M")
    return (
        f"🔴 <b>做空信号 — Kraken</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"📌 形态：📉 看跌吞没\n"
        f"💰 交易对：<b>{symbol}</b>\n"
        f"⚡️ 最高杠杆：{max_lev}x\n"
        f"⏱ 时间级别：{interval_label}\n"
        f"🕐 信号时间：{dt} UTC\n"
        f"━━━━━━━━━━━━━━\n"
        f"💵 入场价：<code>${price:,.4f}</code>\n"
        f"🎯 止盈价：<code>${tp_price:,.4f}</code>  (-{TAKE_PROFIT_PCT}%)\n"
        f"🛑 止损价：<code>${sl_price:,.4f}</code>  (+{STOP_LOSS_PCT}%)\n"
        f"⏰ 建议平仓：<b>{close_time} UTC</b>（4小时后）\n"
        f"━━━━━━━━━━━━━━\n"
        f"✅ 局部反弹结构\n"
        f"✅ 红柱实体完全吞没绿柱\n"
        f"✅ 实体顶部平行（≤1.5%）\n"
        f"✅ 红柱引线突破绿柱引线\n"
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

    async with aiohttp.ClientSession() as session:
        watch_list = await fetch_margin_usd_pairs(session)
        if not watch_list:
            watch_list = [
                {"symbol": "SOLUSD",  "pair": "SOL/USD",  "interval": 60, "max_lev": 10},
                {"symbol": "ETHUSD",  "pair": "ETH/USD",  "interval": 60, "max_lev": 10},
                {"symbol": "BTCUSD",  "pair": "XBT/USD",  "interval": 60, "max_lev": 10},
            ]

        await send_telegram(session,
            f"🤖 <b>做空信号 Bot 已启动</b>\n"
            f"📌 形态：看跌吞没\n"
            f"🎯 止盈：{TAKE_PROFIT_PCT}%  🛑 止损：{STOP_LOSS_PCT}%\n"
            f"📊 共监控 <b>{len(watch_list)}</b> 个杠杆 USD 交易对\n"
            f"扫描间隔：每{POLL_INTERVAL//60}分钟"
        )

        last_refresh = time.time()

        while True:
            if time.time() - last_refresh > 86400:
                new_list = await fetch_margin_usd_pairs(session)
                if new_list:
                    watch_list = new_list
                    last_refresh = time.time()

            log.info(f"--- 开始扫描 {len(watch_list)} 个交易对 ---")
            for watch in watch_list:
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
                            await send_telegram(session, format_alert(
                                watch["symbol"], interval_label, price, dt, watch["max_lev"], latest["open_time"]
                            ))

                except Exception as e:
                    log.error(f"处理 {watch['pair']} 出错: {e}")

                await asyncio.sleep(1.5)

            log.info(f"扫描完成，{POLL_INTERVAL}秒后下次扫描...")
            await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    asyncio.run(run())
