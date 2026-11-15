import ccxt
import pandas as pd
import ta
import requests
import time
from datetime import datetime

# ==========================================
# 你的專屬 Telegram 設定
# ==========================================
TELEGRAM_TOKEN = "916280041:AAHMmgcMylC2k7_SwyjD9gaPTztW6u2GQRo"
CHAT_ID = "7686655302"

def send_tg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print("發送通知失敗:", e)

# 初始化交易所
exchange = ccxt.okx({'options': {'defaultType': 'swap'}})

def get_klines(symbol, timeframe, limit=150):
    bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    return df

def get_top_liquid_altcoins(top_n=50):
    """自動撈取 24H 成交量最高的前 50 大高流動性山寨幣"""
    tickers = exchange.fetch_tickers()
    usdt_swaps = []
    exclude = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'USDC/USDT:USDT']
    for symbol, data in tickers.items():
        if symbol.endswith('/USDT:USDT') and symbol not in exclude:
            vol = data.get('quoteVolume', 0)
            if vol and vol > 10000000:
                usdt_swaps.append((symbol, vol))
    usdt_swaps.sort(key=lambda x: x[1], reverse=True)
    return [x[0] for x in usdt_swaps[:top_n]]

def analyze_symbol(symbol):
    try:
        # 1. 檢查高週期 (15m) MACD 是否偏空
        df_15m = get_klines(symbol, "15m", limit=100)
        macd_15m = ta.trend.MACD(df_15m['close'])
        if macd_15m.macd_diff().iloc[-2] >= 0:
            return None

        # 2. 取得短週期 (5m) K線並計算均線與成交量
        df_5m = get_klines(symbol, "5m", limit=150)
        df_5m['ema7'] = ta.trend.ema_indicator(df_5m['close'], window=7)
        df_5m['ema22'] = ta.trend.ema_indicator(df_5m['close'], window=22)
        df_5m['ema85'] = ta.trend.ema_indicator(df_5m['close'], window=85)
        df_5m['ema120'] = ta.trend.ema_indicator(df_5m['close'], window=120)
        df_5m['vol_ma20'] = df_5m['volume'].rolling(20).mean()

        curr = df_5m.iloc[-2]
        prev = df_5m.iloc[-3]

        # 條件 A：EMA 7 剛下穿 EMA 22（死叉）
        death_cross = (prev['ema7'] >= prev['ema22']) and (curr['ema7'] < curr['ema22'])
        if not death_cross:
            return None

        # 條件 B：成交量濾網（死叉當根成交量大於 20 期均量）
        if curr['volume'] <= curr['vol_ma20']:
            return None

        # 條件 C：空間濾網（現價在 EMA 85/120 之上，距離大均線 > 1.2%）
        price = curr['close']
        support = max(curr['ema85'], curr['ema120'])
        distance_pct = ((price - support) / price) * 100

        if distance_pct > 1.2:
            coin_name = symbol.split('/')[0]
            return (
                f"🚨 <b>符合做空條件：{coin_name}</b>\n"
                f"• 現價：<code>{price}</code>\n"
                f"• 15m MACD：偏空向下\n"
                f"• 5m 訊號：EMA 7/22 帶量死叉\n"
                f"• 下方支撐距離：<b>{distance_pct:.2f}%</b>（空間充足）\n"
                f"• 建議風控：手動以前高為防守點"
            )
    except Exception:
        pass
    return None

if __name__ == "__main__":
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 雲端排程啟動，開始掃描前 50 大高流動性山寨幣...")
    coins = get_top_liquid_altcoins(50)
    found_count = 0
    for sym in coins:
        alert_msg = analyze_symbol(sym)
        if alert_msg:
            send_tg(alert_msg)
            found_count += 1
            print(f"已發送通知：{sym}")
        time.sleep(0.3)
    print(f"本輪掃描完畢，共發送 {found_count} 則通知。")
