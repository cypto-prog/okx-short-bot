import os
import time
from datetime import datetime
import requests
import ccxt
import pandas as pd
import pandas_ta as ta

# ================= 參數設定 =================
TELEGRAM_TOKEN = "916280041:AAHMmgcMylC2k7_SwyjD9gaPTztW6u2GQRo"
CHAT_ID = "7686655302"

def send_tg(message):
    """發送 Telegram 通知"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram 發送失敗: {e}")

# 初始化交易所
exchange = ccxt.okx({'options': {'defaultType': 'swap'}})

def get_ohlcv(symbol, timeframe, limit=150):
    """取得 K 線數據"""
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except Exception:
        return None

def get_top_liquid_altcoins(n=50):
    """自動撈取 24H 成交量最高的前 50 大高流動性山寨幣"""
    try:
        tickers = exchange.fetch_tickers()
        stable_coins = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'USDC/USDT:USDT']
        
        usdt_pairs = []
        for symbol, ticker in tickers.items():
            if not symbol.endswith('/USDT:USDT'):
                continue
            if symbol in stable_coins:
                continue
            vol = ticker.get('quoteVolume', 0)
            if vol > 10000000:
                usdt_pairs.append((symbol, vol))
        
        usdt_pairs.sort(key=lambda x: x[1], reverse=True)
        return [x[0] for x in usdt_pairs[:n]]
    except Exception as e:
        print(f"取得幣種清單失敗: {e}")
        return []

def analyze_symbol(symbol):
    try:
        df_15m = get_ohlcv(symbol, '15m', limit=100)
        macd_15m = ta.macd(df_15m['close'])
        if macd_15m['MACD_12_26_9'].iloc[-2] > 0:
            return None
            
        df_5m = get_ohlcv(symbol, '5m', limit=100)
        ema_7 = ta.ema(df_5m['close'], length=7)
        ema_22 = ta.ema(df_5m['close'], length=22)
        ema_85 = ta.ema(df_5m['close'], length=85)
        ema_120 = ta.ema(df_5m['close'], length=120)
        vol_ma = ta.sma(df_5m['volume'], length=20)
        
        is_dead_cross = (ema_7.iloc[-3] >= ema_22.iloc[-3]) and (ema_7.iloc[-2] < ema_22.iloc[-2])
        if not is_dead_cross:
            return None
            
        if df_5m['volume'].iloc[-2] <= vol_ma.iloc[-2]:
            return None
            
        price = df_5m['close'].iloc[-2]
        base_ema = max(ema_85.iloc[-2], ema_120.iloc[-2])
        distance_pct = (base_ema - price) / price * 100
        
        if distance_pct < 1.2:
            return None
            
        coin_name = symbol.split('/')[0]
        return (
            f"🚨 <b>符合做空條件：{coin_name}</b>\n"
            f"• 現價：<code>{price}</code>\n"
            f"• 15m MACD: 偏空向下\n"
            f"• 5m 訊號：EMA 7/22 帶量死叉\n"
            f"• 下方支撐距離：<b>{distance_pct:.2f}%</b> (空間充足)\n"
            f"• 建議風控：手動以前高為防守點"
        )
    except Exception:
        return None

if __name__ == "__main__":
    send_tg("🔔 <b>GitHub 雲端測試訊息：機器人連線成功！</b>")
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
