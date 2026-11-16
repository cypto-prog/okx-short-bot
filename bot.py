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

# ===== 自定義技術指標函數 =====
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

    print(f"開始掃描共 {len(target_symbols)} 個幣種（優化均線發散區間）...")

    for symbol in target_symbols:
        try:
            ohlcv_1h = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=100)
            ohlcv_15m = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=100)
            ohlcv_5m = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=100)
            
            df_1h = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_15m = pd.DataFrame(ohlcv_15m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_5m = pd.DataFrame(ohlcv_5m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            if len(df_1h) < 30 or len(df_15m) < 30 or len(df_5m) < 30:
                continue

            m_1h, s_1h = calc_macd(df_1h['close'], 10, 20, 7)
            m_15m, s_15m = calc_macd(df_15m['close'], 10, 20, 7)
            m_5m, s_5m = calc_macd(df_5m['close'], 10, 20, 7)

            is_1h_bearish = m_1h.iloc[-2] < s_1h.iloc[-2]
            is_15m_bearish = m_15m.iloc[-2] < s_15m.iloc[-2]

            # ==========================================
            # 策略組合一：1H（大趨勢） + 15M（進場點）
            # ==========================================
            if is_1h_bearish:
                df_15m['ema7'] = calc_ema(df_15m['close'], 7)
                df_15m['ema22'] = calc_ema(df_15m['close'], 22)
                df_15m['ema85'] = calc_ema(df_15m['close'], 85)
                df_15m['ema120'] = calc_ema(df_15m['close'], 120)
                df_15m['rsi'] = calc_rsi(df_15m['close'], 6)
                df_15m['atr'] = calc_atr(df_15m, 14)

                c15_close = df_15m['close'].iloc[-2]
                e7_curr = df_15m['ema7'].iloc[-2]
                e7_prev = df_15m['ema7'].iloc[-3]
                e22_curr = df_15m['ema22'].iloc[-2]
                e22_prev = df_15m['ema22'].iloc[-3]

                cross_15m = (e7_prev >= e22_prev) and (e7_curr < e22_curr)
                p_below_15 = c15_close < e22_curr
                macd_b_15 = m_15m.iloc[-2] < s_15m.iloc[-2]
                rsi_ok_15 = df_15m['rsi'].iloc[-2] < 75

                # 調整後的適度發散檢查（大於 0.05% 即符合，避免過度黏合但也不用拉太開）
                ema_spread_15 = abs(e7_curr - e22_curr) / c15_close
                not_too_flat_15 = ema_spread_15 >= 0.0005

                r85 = (c15_close - df_15m['ema85'].iloc[-2]) / df_15m['ema85'].iloc[-2]
                r120 = (c15_close - df_15m['ema120'].iloc[-2]) / df_15m['ema120'].iloc[-2]
                room_ok_15 = (r85 >= 0.01) and (r120 >= 0.01)

                if cross_15m and p_below_15 and macd_b_15 and rsi_ok_15 and room_ok_15 and not_too_flat_15:
                    atr_val = df_15m['atr'].iloc[-2]
                    sl = c15_close + (atr_val * 1.1)
                    tp = c15_close - (atr_val * 1.6)
                    
                    msg = (
                        f"🔵 **【OKX 空單訊號：1H + 15M 策略】** 🔵\n\n"
                        f"🔹 **幣種**: `{symbol}`\n"
                        f"📊 **時區組合**: `1H 大趨勢 + 15M 適度發散死叉`\n"
                        f"💰 **建議進場價**: `{c15_close}`\n"
                        f"🛑 **建議停損 (1.1x ATR)**: `{sl:.4f}`\n"
                        f"🎯 **建議止盈 (1.6x ATR)**: `{tp:.4f}`"
                    )
                    send_telegram_alert(msg)

            # ==========================================
            # 策略組合二：15M（大趨勢） + 5M（進場點）
            # ==========================================
            if is_15m_bearish:
                df_5m['ema7'] = calc_ema(df_5m['close'], 7)
                df_5m['ema22'] = calc_ema(df_5m['close'], 22)
                df_5m['ema85'] = calc_ema(df_5m['close'], 85)
                df_5m['ema120'] = calc_ema(df_5m['close'], 120)
                df_5m['rsi'] = calc_rsi(df_5m['close'], 6)
                df_5m['atr'] = calc_atr(df_5m, 14)

                c5_close = df_5m['close'].iloc[-2]
                e7_c5 = df_5m['ema7'].iloc[-2]
                e7_p5 = df_5m['ema7'].iloc[-3]
                e22_c5 = df_5m['ema22'].iloc[-2]
                e22_p5 = df_5m['ema22'].iloc[-3]

                cross_5m = (e7_p5 >= e22_p5) and (e7_c5 < e22_c5)
                p_below_5 = c5_close < e22_c5
                macd_b_5 = m_5m.iloc[-2] < s_5m.iloc[-2]
                rsi_ok_5 = df_5m['rsi'].iloc[-2] < 75

                # 調整後的適度發散檢查（大於 0.05% 即符合）
                ema_spread_5 = abs(e7_c5 - e22_c5) / c5_close
                not_too_flat_5 = ema_spread_5 >= 0.0005

                r85_5 = (c5_close - df_5m['ema85'].iloc[-2]) / df_5m['ema85'].iloc[-2]
                r120_5 = (c5_close - df_5m['ema120'].iloc[-2]) / df_5m['ema120'].iloc[-2]
                room_ok_5 = (r85_5 >= 0.01) and (r120_5 >= 0.01)

                if cross_5m and p_below_5 and macd_b_5 and rsi_ok_5 and room_ok_5 and not_too_flat_5:
                    atr_val_5 = df_5m['atr'].iloc[-2]
                    sl_5 = c5_close + (atr_val_5 * 1.1)
                    tp_5 = c5_close - (atr_val_5 * 1.6)
                    
                    msg = (
                        f"🟢 **【OKX 空單訊號：15M + 5M 策略】** 🟢\n\n"
                        f"🔹 **幣種**: `{symbol}`\n"
                        f"📊 **時區組合**: `15M 大趨勢 + 5M 適度發散死叉`\n"
                        f"💰 **建議進場價**: `{c5_close}`\n"
                        f"🛑 **建議停損 (1.1x ATR)**: `{sl_5:.4f}`\n"
                        f"🎯 **建議止盈 (1.6x ATR)**: `{tp_5:.4f}`"
                    )
                    send_telegram_alert(msg)

            time.sleep(0.5)

        except Exception as e:
            print(f"處理 {symbol} 時發生錯誤: {e}")
            continue

if __name__ == "__main__":
    check_market_scanner()