"""
Crypto K-Line Short Signal Bot — Kraken Edition
启动时自动从 Kraken API 获取最新支持杠杆的 USD 交易对
严格版看跌吞没5条件做空信号
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

# 止盈止损
TAKE_PROFIT_PCT = 1.3
STOP_LOSS_PCT   = 1.0

# 检测参数
PARAMS = {
    "min_green_candles":  3,    # 前方至少3根绿柱逐渐上升
    "parallel_top_pct":   1.5,  # 两根顶部差距 <= 1.5%
    "volume_surge_ratio": 1.2,  # 成交量 >= 前5根均量 × 1.2
}

POLL_INTERVAL = 120  # 每2分钟扫一次

# ─── 自动获取 Kraken 杠杆 USD 交易对 ─────────────────────────────────────────

KRAKEN_ASSET_PAIRS_URL = "https://api.kraken.com/0/public/AssetPairs"
KRAKEN_OHLC_URL        = "https://api.kraken.com/0/public/OHLC"

async def fetch_margin_usd_pairs(session):
    """
    从 Kraken API 实时获取所有支持杠杆的 USD 交易对
    过滤条件：quote=ZUSD 或 USD，leverage_buy 不为空
    排除稳定币对稳定币
    """
    try:
        async with session.get(
            KRAKEN_ASSET_PAIRS_URL,
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            data = await resp.json()
            if data.get("error"):
                log.error(f"获取交易对失败: {data['error']}")
                return []

            pairs = []
            for pair_name, info in data["result"].items():
                # 只要 USD 计价
                quote = info.get("quote", "")
                if quote not in ("ZUSD", "USD"):
                    continue
                # 必须支持杠杆
                leverage_buy = info.get("leverage_buy", [])
                if not leverage_buy:
                    continue
                # 排除暗池（.d结尾）
                if pair_name.endswith(".d"):
                    continue
                # 排除稳定币（USDT/USDC/DAI/PAX 本身）
                base = info.get("base", "")
                stables = {"USDT", "USDC", "DAI", "ZUSD", "PAXG", "XAUT"}
                if base in stables:
                    continue

                # 用 altname 作为 API 查询用的 pair
                altname = info.get("altname", pair_name)
                symbol = altname.replace("/", "").replace("XBT", "BTC")

                pairs.append({
                    "symbol":   symbol,
                    "pair":     altname,
                    "interval": 60,
                    "max_lev":  max(leverage_buy),
                })

            # 按最大杠杆倍数排序，优先监控杠杆高的
            pairs.sort(key=lambda x: x["max_lev"], reverse=True)
            log.info(f"✅ 获取到 {len(pairs)} 个支持杠杆的 USD 交易对")
            return pairs

    except Exception as e:
        log.error(f"获取交易对异常: {e}")
        return []

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

# ─── 严格版看跌吞没检测（5条件）─────────────────────────────────────────────────

def detect_bearish_engulfing(candles):
    """
    条件1：前方至少3根绿柱收盘价逐渐升高
    条件2：前根阳线（绿柱）
    条件3：当根阴线实体完全吞没前根阳线实体
    条件4：两根顶部平行（差距 <= 1.5%）
    条件5：红柱最高价严格大于绿柱最高价（上引线突破）
    +成交量放大
    """
    if len(candles) < 8:
        return False

    prev = candles[-2]
    curr = candles[-1]

    # 条件2：前根阳线
    if prev["close"] <= prev["open"]:
        return False
    # 条件2：当根阴线
    if curr["close"] >= curr["open"]:
        return False

    # 条件1：前3根绿柱收盘逐渐升高且都是阳线
    n = PARAMS["min_green_candles"]
    prior = candles[-(2 + n):-2]
    if len(prior) < n:
        return False
    for c in prior:
        if c["close"] <= c["open"]:
            return False
    for i in range(1, len(prior)):
        if prior[i]["close"] <= prior[i-1]["close"]:
            return False

    # 条件3：实体完全吞没
    if curr["close"] >= prev["open"]:
        return False

    # 条件4：顶部平行（差距 <= 1.5%）
    high_diff_pct = abs(curr["high"] - prev["high"]) / prev["high"] * 100
    if high_diff_pct > PARAMS["parallel_top_pct"]:
        return False

    # 条件5：红柱最高价严格 > 绿柱最高价
    if curr["high"] <= prev["high"]:
        return False

    # 成交量放大
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

def format_alert(symbol, interval_label, price, dt, max_lev):
    tp_price = price * (1 - TAKE_PROFIT_PCT / 100)
    sl_price = price * (1 + STOP_LOSS_PCT   / 100)
    return (
        f"🔴 <b>做空信号 — Kraken</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"📌 形态：📉 看跌吞没\n"
        f"💰 交易对：<b>{symbol}</b>\n"
        f"⚡️ 最高杠杆：{max_lev}x\n"
        f"⏱ 时间级别：{interval_label}\n"
        f"🕐 {dt} UTC\n"
        f"━━━━━━━━━━━━━━\n"
        f"💵 入场价：<code>${price:,.4f}</code>\n"
        f"🎯 止盈价：<code>${tp_price:,.4f}</code>  (-{TAKE_PROFIT_PCT}%)\n"
        f"🛑 止损价：<code>${sl_price:,.4f}</code>  (+{STOP_LOSS_PCT}%)\n"
        f"━━━━━━━━━━━━━━\n"
        f"✅ 前3根绿柱逐渐上升\n"
        f"✅ 红柱实体完全吞没绿柱\n"
        f"✅ 顶部平行（阻力位）\n"
        f"✅ 红柱上引线突破绿柱\n"
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
    log.info("🚀 做空信号 Bot 启动")

    async with aiohttp.ClientSession() as session:
        # 启动时自动获取最新杠杆交易对
        watch_list = await fetch_margin_usd_pairs(session)
        if not watch_list:
            log.error("无法获取交易对，使用备用列表")
            watch_list = [
                {"symbol": "SOLUSD",  "pair": "SOL/USD",  "interval": 60, "max_lev": 10},
                {"symbol": "ETHUSD",  "pair": "ETH/USD",  "interval": 60, "max_lev": 10},
                {"symbol": "BTCUSD",  "pair": "XBT/USD",  "interval": 60, "max_lev": 10},
            ]

        pairs_str = ", ".join(w["symbol"] for w in watch_list[:10]) + f"... 共{len(watch_list)}个"
        log.info(f"监控列表: {pairs_str}")

        await send_telegram(session,
            f"🤖 <b>做空信号 Bot 已启动</b>\n"
            f"📌 形态：看跌吞没（严格5条件）\n"
            f"🎯 止盈：{TAKE_PROFIT_PCT}%  🛑 止损：{STOP_LOSS_PCT}%\n"
            f"📊 自动获取 Kraken 杠杆 USD 交易对：共 <b>{len(watch_list)}</b> 个\n"
            f"扫描间隔：每{POLL_INTERVAL//60}分钟"
        )

        # 每24小时刷新一次交易对列表
        last_refresh = time.time()

        while True:
            # 每24小时重新拉取最新交易对
            if time.time() - last_refresh > 86400:
                log.info("刷新交易对列表...")
                new_list = await fetch_margin_usd_pairs(session)
                if new_list:
                    watch_list = new_list
                    last_refresh = time.time()
                    log.info(f"交易对已更新，共 {len(watch_list)} 个")

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
                                watch["symbol"], interval_label, price, dt, watch["max_lev"]
                            ))

                except Exception as e:
                    log.error(f"处理 {watch['pair']} 出错: {e}")

                await asyncio.sleep(1.5)  # 避免触发限速

            log.info(f"扫描完成，{POLL_INTERVAL}秒后下次扫描...")
            await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    asyncio.run(run())
