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

def calculate_rsi(series, period):
    """純 Pandas 計算指定週期的 RSI"""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

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
        
        # ================= 策略 A：1H 搭配 15M (結合 RSI 3線過熱反轉) =================
        df_1h = get_ohlcv(symbol, '1h', limit=150)
        df_15m_a = get_ohlcv(symbol, '15m', limit=150)
        if df_1h is not None and df_15m_a is not None:
            macd_1h = calculate_macd(df_1h['close'])
            
            # 計算 15M 的 RSI 6, 12, 24
            rsi_6 = calculate_rsi(df_15m_a['close'], 6)
            rsi_12 = calculate_rsi(df_15m_a['close'], 12)
            rsi_24 = calculate_rsi(df_15m_a['close'], 24)
            
            if macd_1h.iloc[-2] < 0:
                # 條件：上一根或當下，3條 RSI 曾經全部 > 70 (超買區)
                was_overbought = (rsi_6.iloc[-2] > 70) and (rsi_12.iloc[-2] > 70) and (rsi_24.iloc[-2] > 70)
                
                # 交叉條件：RSI 6 原本在上方，現在向下穿破 RSI 12 與 RSI 24
                rsi_cross_down = (rsi_6.iloc[-3] >= rsi_12.iloc[-3]) and (rsi_6.iloc[-2] < rsi_12.iloc[-2])
                
                if was_overbought and rsi_cross_down:
                    price = df_15m_a['close'].iloc[-2]
                    r6, r12, r24 = round(rsi_6.iloc[-2], 1), round(rsi_12.iloc[-2], 1), round(rsi_24.iloc[-2], 1)
                    return (
                        f"🚨 <b>[1H+15M RSI 超買過熱反轉]：{coin_name}</b>\n"
                        f"• 現價：<code>{price}</code>\n"
                        f"• RSI 狀態：三線曾超買 (>70) 且 6快線向下穿破\n"
                        f"• 當前數值：RSI6({r6}) | RSI12({r12}) | RSI24({r24})\n"
                        f"• 1H 大趨勢：MACD 偏空向下"
                    )

        # ================= 策略 B：15M 搭配 5M (結合 RSI 3線過熱反轉) =================
        df_15m_b = get_ohlcv(symbol, '15m', limit=150)
        df_5m = get_ohlcv(symbol, '5m', limit=150)
        if df_15m_b is not None and df_5m is not None:
            macd_15m = calculate_macd(df_15m_b['close'])
            
            # 計算 5M 的 RSI 6, 12, 24
            rsi_6_5m = calculate_rsi(df_5m['close'], 6)
            rsi_12_5m = calculate_rsi(df_5m['close'], 12)
            rsi_24_5m = calculate_rsi(df_5m['close'], 24)
            
            if macd_15m.iloc[-2] < 0:
                was_overbought_5m = (rsi_6_5m.iloc[-2] > 70) and (rsi_12_5m.iloc[-2] > 70) and (rsi_24_5m.iloc[-2] > 70)
                rsi_cross_down_5m = (rsi_6_5m.iloc[-3] >= rsi_12_5m.iloc[-3]) and (rsi_6_5m.iloc[-2] < rsi_12_5m.iloc[-2])
                
                if was_overbought_5m and rsi_cross_down_5m:
                    price = df_5m['close'].iloc[-2]
                    r6, r12, r24 = round(rsi_6_5m.iloc[-2], 1), round(rsi_12_5m.iloc[-2], 1), round(rsi_24_5m.iloc[-2], 1)
                    return (
                        f"⚡ <b>[15M+5M 短線 RSI 超買反轉]：{coin_name}</b>\n"
                        f"• 現價：<code>{price}</code>\n"
                        f"• RSI 狀態：三線曾超買 (>70) 且 6快線向下穿破\n"
                        f"• 當前數值：RSI6({r6}) | RSI12({r12}) | RSI24({r24})\n"
                        f"• 15M 趨勢：MACD 偏空向下"
                    )

        return None
    except Exception:
        return None

if __name__ == "__main__":
    print(f"[{datetime.now().strftime('%H:%M:%S')}] RSI 多維雷達掃描開始...")
    coins = get_top_liquid_altcoins(50)
    found_count = 0
    
    for sym in coins:
        alert_msg = analyze_symbol(sym)
        if alert_msg:
            send_tg(alert_msg)
            found_count += 1
            print(f"發送 RSI 觀察通知：{sym}")
        time.sleep(0.3)
        
    print(f"本輪掃描完畢。共發送 {found_count} 筆 RSI 反轉訊號。")
