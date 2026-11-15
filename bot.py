import os
import time
from datetime import datetime
import requests
import ccxt
import pandas as pd

# ================= 參數設定 =================
TELEGRAM_TOKEN = "9295995564:AAH8PkxghF_ngjZwMVpLUmMTpJ9rVx0EtYY"
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

def calculate_macd(series, fast=12, slow=26, signal=9):
    """純 Pandas 計算 MACD"""
    exp1 = series.ewm(span=fast, adjust=False).mean()
    exp2 = series.ewm(span=slow, adjust=False).mean()
    macd_line = exp1 - exp2
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line - signal_line

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
        coin_name = symbol.split('/')[0]
        
        # ================= 策略 A：1H 搭配 15M (放寬版：只要死叉就報) =================
        df_1h = get_ohlcv(symbol, '1h', limit=100)
        df_15m_a = get_ohlcv(symbol, '15m', limit=100)
        if df_1h is not None and df_15m_a is not None:
            macd_1h = calculate_macd(df_1h['close'])
            ema_7_15 = df_15m_a['close'].ewm(span=7, adjust=False).mean()
            ema_22_15 = df_15m_a['close'].ewm(span=22, adjust=False).mean()
            
            # 放寬條件：只要 1H MACD 小於 0，且 15M 發生 EMA 死叉就抓出來
            if macd_1h.iloc[-2] < 0:
                is_dead_cross_15 = (ema_7_15.iloc[-3] >= ema_22_15.iloc[-3]) and (ema_7_15.iloc[-2] < ema_22_15.iloc[-2])
                if is_dead_cross_15:
                    price = df_15m_a['close'].iloc[-2]
                    return (
                        f"📊 <b>[觀察區：1H + 15M 做空雷達]：{coin_name}</b>\n"
                        f"• 現價：<code>{price}</code>\n"
                        f"• 1H 大趨勢：MACD 偏空\n"
                        f"• 15M 狀態：EMA 7/22 剛好死叉\n"
                        f"• 提示：已放寬條件，請自行斟酌觀察"
                    )

        # ================= 策略 B：15M 搭配 5M (放寬版：短線高敏感) =================
        df_15m_b = get_ohlcv(symbol, '15m', limit=100)
        df_5m = get_ohlcv(symbol, '5m', limit=100)
        if df_15m_b is not None and df_5m is not None:
            macd_15m = calculate_macd(df_15m_b['close'])
            ema_7_5 = df_5m['close'].ewm(span=7, adjust=False).mean()
            ema_22_5 = df_5m['close'].ewm(span=22, adjust=False).mean()
            
            # 放寬條件：只要 15M MACD 小於 0，且 5M 發生 EMA 死叉就抓出來
            if macd_15m.iloc[-2] < 0:
                is_dead_cross_5 = (ema_7_5.iloc[-3] >= ema_22_5.iloc[-3]) and (ema_7_5.iloc[-2] < ema_22_5.iloc[-2])
                if is_dead_cross_5:
                    price = df_5m['close'].iloc[-2]
                    return (
                        f"⚡ <b>[觀察區：15M + 5M 短線雷達]：{coin_name}</b>\n"
                        f"• 現價：<code>{price}</code>\n"
                        f"• 15M 趨勢：MACD 偏空\n"
                        f"• 5M 狀態：EMA 7/22 剛好死叉\n"
                        f"• 提示：短線高敏感捕捉，請自行觀察"
                    )

        return None
    except Exception:
        return None

if __name__ == "__main__":
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 高敏感掃描開始...")
    coins = get_top_liquid_altcoins(50)
    found_count = 0
    
    for sym in coins:
        alert_msg = analyze_symbol(sym)
        if alert_msg:
            send_tg(alert_msg)
            found_count += 1
            print(f"發送觀察通知：{sym}")
        time.sleep(0.3)
        
    print(f"本輪掃描完畢。共發送 {found_count} 筆觀察訊號。")
