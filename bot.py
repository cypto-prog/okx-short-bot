import os
import ccxt
import pandas as pd
import requests

# 1. 取得 Telegram 設定
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram 密鑰未設定，略過發送。")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("Telegram 通知發送成功！")
        else:
            print(f"Telegram 發送失敗: {response.text}")
    except Exception as e:
        print(f"發送 Telegram 發生錯誤: {e}")

def calculate_indicators(df):
    df['EMA_7'] = df['close'].ewm(span=7, adjust=False).mean()
    df['EMA_22'] = df['close'].ewm(span=22, adjust=False).mean()
    df['EMA_85'] = df['close'].ewm(span=85, adjust=False).mean()
    df['EMA_120'] = df['close'].ewm(span=120, adjust=False).mean()

    # MACD 參數已改為 10, 20, 7
    exp1 = df['close'].ewm(span=10, adjust=False).mean()
    exp2 = df['close'].ewm(span=20, adjust=False).mean()
    df['MACD'] = exp1 - exp2
    df['MACD_Signal'] = df['MACD'].ewm(span=7, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    df['ATR'] = true_range.rolling(window=14).mean()

    return df

def scan_market():
    exchange = ccxt.okx({
        'options': {
            'defaultType': 'swap',
        }
    })
    
    try:
        exchange.load_markets()
    except Exception as e:
        print(f"載入 OKX 市場失敗: {e}")
        return

    # 取得成交量前 100 大的主流幣種
    tickers = exchange.fetch_tickers()
    usdt_swaps = []
    
    for symbol, ticker in tickers.items():
        if symbol.endswith('/USDT:USDT') or (symbol.endswith('USDT') and ':USDT' in symbol):
            quote_volume = ticker.get('quoteVolume', 0) or 0
            usdt_swaps.append((symbol, quote_volume))

    usdt_swaps.sort(key=lambda x: x[1], reverse=True)
    symbols = [item[0] for item in usdt_swaps[:100]]

    print(f"開始掃描 OKX 前 100 大合約【純均線死叉 + MACD死叉(10,20,7) 空單策略】...")

    for symbol in symbols:
        try:
            ohlcv_1h = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=150)
            df_1h = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_1h = calculate_indicators(df_1h)

            ohlcv_15m = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=150)
            df_15m = pd.DataFrame(ohlcv_15m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_15m = calculate_indicators(df_15m)

            ohlcv_5m = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=150)
            df_5m = pd.DataFrame(ohlcv_5m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_5m = calculate_indicators(df_5m)

            if len(df_1h) < 5 or len(df_15m) < 5 or len(df_5m) < 5:
                continue

            r_1h = df_1h.iloc[-2]
            r_15m = df_15m.iloc[-2]
            r_5m = df_5m.iloc[-2]
            r_15m_prev = df_15m.iloc[-3]

            # --- 策略一：1h 配合 15m (僅留均線與 MACD) ---
            track_1 = (
                (r_1h['EMA_7'] < r_1h['EMA_22']) and 
                (r_15m['EMA_7'] < r_15m['EMA_22']) and 
                (r_15m['MACD_Hist'] < 0)
            )

            # --- 策略二：15m 配合 5m (僅留均線與 MACD) ---
            track_2 = (
                (r_15m['EMA_7'] < r_15m['EMA_22']) and 
                (r_5m['EMA_7'] < r_5m['EMA_22']) and 
                (r_5m['MACD_Hist'] < 0)
            )

            # --- 策略三：15m 獨立自主觸發 (均線死叉 + MACD死叉) ---
            is_ema_dead_cross_15m = (r_15m['EMA_7'] < r_15m['EMA_22'])
            is_macd_cross_or_ready_15m = (
                (r_15m['MACD'] < r_15m['MACD_Signal']) or 
                (r_15m_prev['MACD_Hist'] >= 0 and r_15m['MACD_Hist'] < 0)
            )
            track_3 = is_ema_dead_cross_15m and is_macd_cross_or_ready_15m

            if track_1 or track_2 or track_3:
                close_price = r_5m['close']
                atr = r_5m['ATR']
                stop_loss = close_price + (1.1 * atr)
                take_profit = close_price - (1.6 * atr)

                trigger_source = []
                if track_1: trigger_source.append("1h+15m共振(空)")
                if track_2: trigger_source.append("15m+5m共振(空)")
                if track_3: trigger_source.append("15m獨立(均線死叉+MACD死叉)")

                msg = (
                    f"🚨 **OKX 智能空單訊號觸發** 🚨\n\n"
                    f"📌 **標的**: `{symbol}`\n"
                    f"⏰ **觸發來源**: `{', '.join(trigger_source)}`\n"
                    f"💰 **建議進場價**: `{close_price}`\n"
                    f"🛑 **建議止損 (SL)**: `{stop_loss:.4f}` (1.1x ATR)\n"
                    f"🎯 **建議指盈 (TP)**: `{take_profit:.4f}` (1.6x ATR)"
                )
                send_telegram_message(msg)
                print(f"發現符合空單條件標的: {symbol} [{', '.join(trigger_source)}]")

        except Exception as e:
            print(f"處理標的 {symbol} 時發生錯誤: {e}")

if __name__ == "__main__":
    scan_market()