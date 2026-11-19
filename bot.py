import os
import time
from datetime import datetime
import requests
import ccxt
import pandas as pd

# ================= 參數設定 =================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or "9295995564:AAH8PkxghF_ngjZwMVpLUmMTpJ9rVx0EtYY"
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or "7686655302"

TIMEFRAMES = ["5m", "15m", "1h"]
TOP_N = 100


def send_tg(message):
    """發送 Telegram 通知"""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram 設定不完整，略過發送。")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print("Telegram 通知發送成功。")
        else:
            print(f"Telegram 發送失敗 ({resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"Telegram 發送失敗: {e}")


exchange = ccxt.okx({'options': {'defaultType': 'swap'}})


def get_ohlcv(symbol, timeframe, limit=150):
    """取得 K 線數據"""
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        if not bars:
            return None
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except Exception:
        return None


def get_top_usdt_swaps(n=100):
    """撈取成交量前 N 大 USDT 永續合約"""
    try:
        exchange.load_markets()
        tickers = exchange.fetch_tickers()
        usdt_pairs = []
        for symbol, ticker in tickers.items():
            if symbol.endswith('/USDT:USDT'):
                vol = ticker.get('quoteVolume') or 0
                usdt_pairs.append((symbol, vol))
        usdt_pairs.sort(key=lambda x: x[1], reverse=True)
        return [x[0] for x in usdt_pairs[:n]]
    except Exception as e:
        print(f"取得幣種清單失敗: {e}")
        return []


def check_timeframe_short(df):
    """單一時區獨立檢查：死叉狀態 + K棒收在均線下方"""
    if df is None or len(df) < 25:
        return False, None

    ema_7 = df['close'].ewm(span=7, adjust=False).mean()
    ema_22 = df['close'].ewm(span=22, adjust=False).mean()

    prev_bar = df.iloc[-3]
    curr_bar = df.iloc[-2]

    dead_cross = (ema_7.iloc[-3] >= ema_22.iloc[-3]) and (ema_7.iloc[-2] < ema_22.iloc[-2])
    below_ema = (curr_bar['close'] < ema_7.iloc[-2]) and (curr_bar['close'] < ema_22.iloc[-2])

    if dead_cross and below_ema:
        return True, curr_bar
    return False, None


def scan_market():
    print(f"開始掃描 OKX 前 {TOP_N} 大合約【5m / 15m / 1h 獨立：死叉狀態 + K棒收在均線下方】...")
    symbols = get_top_usdt_swaps(TOP_N)
    found_count = 0

    for symbol in symbols:
        coin_name = symbol.split('/')[0]
        for tf in TIMEFRAMES:
            df = get_ohlcv(symbol, tf, limit=150)
            hit, bar = check_timeframe_short(df)
            if hit and bar is not None:
                found_count += 1
                price = bar['close']
                msg = (
                    f"🔻 <b>[做空訊號：{coin_name}]</b>\n"
                    f"• 時區：<code>{tf}</code>\n"
                    f"• 現價：<code>{price}</code>\n"
                    f"• 條件：EMA 7/22 死叉 + K棒收在均線下方"
                )
                send_tg(msg)
                print(f"發現訊號：{symbol} [{tf}] 價格={price}")
            time.sleep(0.15)

    print(f"本輪掃描完畢。發現訊號數：{found_count} 筆。")


if __name__ == "__main__":
    scan_market()