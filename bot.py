import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import time

# ===== Telegram 設定 (已自動幫你填入) =====
TELEGRAM_TOKEN = "9295995564:AAH8PkxghF_ngjZwMVpLUmMTpJ9rVx0EtYY"
TELEGRAM_CHAT_ID = "7686655302"

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram 發送失敗: {e}")

def check_market_scanner():
    # 初始化 OKX 交易所 (永續合約)
    exchange = ccxt.okx({
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'}
    })
    
    try:
        markets = exchange.load_markets()
    except Exception as e:
        print(f"載入市場失敗: {e}")
        return

    # 篩選出 USDT 永續合約，並取流動性較好的前 80 檔山寨幣
    symbols = [s for s in markets if s.endswith('/USDT:USDT') or s.endswith('/USDT')]
    target_symbols = symbols[:80] 

    print(f"開始掃描共 {len(target_symbols)} 個幣種...")

    for symbol in target_symbols:
        try:
            # 1. 抓取 15m (高週期) 與 5m (本週期) K線數據
            ohlcv_15m = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
            ohlcv_5m = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
            
            df_15m = pd.DataFrame(ohlcv_15m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_5m = pd.DataFrame(ohlcv_5m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            if len(df_15m) < 30 or len(df_5m) < 30:
                continue

            # ===== 2. 計算 15m 高週期 MACD (大方向過濾) =====
            macd_15m = ta.macd(df_15m['close'], fast=10, slow=20, signal=7)
            df_15m = pd.concat([df_15m, macd_15m], axis=1)
            htf_bearish = df_15m['MACD_10_20_7'].iloc[-2] < df_15m['MACDs_10_20_7'].iloc[-2]

            if not htf_bearish:
                continue # 大方向不是空頭，直接跳過

            # ===== 3. 計算 5m 本週期指標 =====
            df_5m['ema7'] = ta.ema(df_5m['close'], length=7)
            df_5m['ema22'] = ta.ema(df_5m['close'], length=22)
            df_5m['ema85'] = ta.ema(df_5m['close'], length=85)
            df_5m['ema120'] = ta.ema(df_5m['close'], length=120)
            df_5m['rsi'] = ta.rsi(df_5m['close'], length=6)
            df_5m['atr'] = ta.atr(df_5m['high'], df_5m['low'], df_5m['close'], length=14)

            macd_5m = ta.macd(df_5m['close'], fast=10, slow=20, signal=7)
            df_5m = pd.concat([df_5m, macd_5m], axis=1)

            close_price = df_5m['close'].iloc[-2]
            ema7_curr = df_5m['ema7'].iloc[-2]
            ema7_prev = df_5m['ema7'].iloc[-3]
            ema22_curr = df_5m['ema22'].iloc[-2]
            ema22_prev = df_5m['ema22'].iloc[-3]
            
            # 判斷 5m 是否剛好發生死叉
            crossover_bear = (ema7_prev >= ema22_prev) and (ema7_curr < ema22_curr)
            
            price_below = close_price < ema22_curr
            macd_bear_5m = df_5m['MACD_10_20_7'].iloc[-2] < df_5m['MACDs_10_20_7'].iloc[-2]
            rsi_valid = df_5m['rsi'].iloc[-2] < 75

            # 檢查下方空間 (距離 EMA 85 / 120 是否大於 1.0%)
            ema85_val = df_5m['ema85'].iloc[-2]
            ema120_val = df_5m['ema120'].iloc[-2]
            room_85 = (close_price - ema85_val) / ema85_val
            room_120 = (close_price - ema120_val) / ema120_val
            has_enough_room = (room_85 >= 0.01) and (room_120 >= 0.01)

            # ===== 4. 總合條件觸發並推播 =====
            if crossover_bear and price_below and macd_bear_5m and rsi_valid and has_enough_room:
                current_atr = df_5m['atr'].iloc[-2]
                sl_price = close_price + (current_atr * 1.0)  # 1倍 ATR 停損
                tp_price = close_price - (current_atr * 2.0)  # 2倍 ATR 止盈

                msg = (
                    f"🚨 **【發現 OKX 潛在短線空單】** 🚨\n\n"
                    f"🔹 **幣種**: `{symbol}`\n"
                    f"💰 **建議進場價**: `{close_price}`\n"
                    f"🛑 **建議停損 (1x ATR)**: `{sl_price:.4f}`\n"
                    f"🎯 **建議止盈 (2x ATR)**: `{tp_price:.4f}`\n\n"
                    f"⚡ *雙週期條件吻合，請自行前往 TradingView / OKX 確認盤面！*"
                )
                send_telegram_alert(msg)
                print(f"已發送推播: {symbol}")

            time.sleep(0.5)

        except Exception as e:
            print(f"處理 {symbol} 時發生錯誤: {e}")
            continue

if __name__ == "__main__":
    check_market_scanner()