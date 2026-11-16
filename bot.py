import ccxt
import pandas as pd
import requests
import time

# ===== Telegram 設定 =====
TELEGRAM_TOKEN = "9295995564:AAH8PkxghF_ngjZwMVpLUmMTpJ9rVx0EtYY"
TELEGRAM_CHAT_ID = "7686655302"

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram 發送失敗: {e}")

# ===== 自定義技術指標函數 (不需安裝 pandas_ta) =====
def calc_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def calc_macd(series, fast=10, slow=20, signal=7):
    ema_fast = calc_ema(series, fast)
    ema_slow = calc_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    return macd_line, signal_line

def calc_rsi(series, length=6):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=length - 1, min_periods=length).mean()
    avg_loss = loss.ewm(com=length - 1, min_periods=length).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_atr(df, length=14):
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(span=length, adjust=False).mean()

def check_market_scanner():
    exchange = ccxt.okx({
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'}
    })
    
    try:
        markets = exchange.load_markets()
    except Exception as e:
        print(f"載入市場失敗: {e}")
        return

    symbols = [s for s in markets if s.endswith('/USDT:USDT') or s.endswith('/USDT')]
    target_symbols = symbols[:80] 

    print(f"開始掃描共 {len(target_symbols)} 個幣種...")

    for symbol in target_symbols:
        try:
            ohlcv_15m = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
            ohlcv_5m = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
            
            df_15m = pd.DataFrame(ohlcv_15m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_5m = pd.DataFrame(ohlcv_5m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            if len(df_15m) < 30 or len(df_5m) < 30:
                continue

            # ===== 15m MACD 過濾 =====
            m_line_15, s_line_15 = calc_macd(df_15m['close'], 10, 20, 7)
            htf_bearish = m_line_15.iloc[-2] < s_line_15.iloc[-2]

            if not htf_bearish:
                continue 

            # ===== 5m 本週期指標 =====
            df_5m['ema7'] = calc_ema(df_5m['close'], 7)
            df_5m['ema22'] = calc_ema(df_5m['close'], 22)
            df_5m['ema85'] = calc_ema(df_5m['close'], 85)
            df_5m['ema120'] = calc_ema(df_5m['close'], 120)
            df_5m['rsi'] = calc_rsi(df_5m['close'], 6)
            df_5m['atr'] = calc_atr(df_5m, 14)

            m_line_5, s_line_5 = calc_macd(df_5m['close'], 10, 20, 7)

            close_price = df_5m['close'].iloc[-2]
            ema7_curr = df_5m['ema7'].iloc[-2]
            ema7_prev = df_5m['ema7'].iloc[-3]
            ema22_curr = df_5m['ema22'].iloc[-2]
            ema22_prev = df_5m['ema22'].iloc[-3]
            
            crossover_bear = (ema7_prev >= ema22_prev) and (ema7_curr < ema22_curr)
            price_below = close_price < ema22_curr
            macd_bear_5m = m_line_5.iloc[-2] < s_line_5.iloc[-2]
            rsi_valid = df_5m['rsi'].iloc[-2] < 75

            ema85_val = df_5m['ema85'].iloc[-2]
            ema120_val = df_5m['ema120'].iloc[-2]
            room_85 = (close_price - ema85_val) / ema85_val
            room_120 = (close_price - ema120_val) / ema120_val
            has_enough_room = (room_85 >= 0.01) and (room_120 >= 0.01)

            if crossover_bear and price_below and macd_bear_5m and rsi_valid and has_enough_room:
                current_atr = df_5m['atr'].iloc[-2]
                sl_price = close_price + (current_atr * 1.0)
                tp_price = close_price - (current_atr * 2.0)

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