import os
import requests
import ccxt
import pandas as pd
import time

# ================= 參數設定 =================
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

RSI_LIMIT = 75
MIN_ROOM_PCT = 0.006       # 0.6% 最小下行空間
MIN_SPREAD = 0.0006        # 均線最小發散幅度
MIN_AWAY_PCT = 0.0005      # 距快線最小分離距離
ATR_LEN = 14
SL_MULT = 1.1              # 1.1x ATR 停損
TP_MULT = 2.0              # 2.0x ATR 止盈

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("未設定 Telegram 密鑰")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram 發送失敗: {e}")

def compute_indicators(df):
    df['ema7'] = df['close'].ewm(span=7, adjust=False).mean()
    df['ema22'] = df['close'].ewm(span=22, adjust=False).mean()
    df['ema85'] = df['close'].ewm(span=85, adjust=False).mean()
    df['ema120'] = df['close'].ewm(span=120, adjust=False).mean()
    
    exp1 = df['close'].ewm(span=10, adjust=False).mean()
    exp2 = df['close'].ewm(span=20, adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['macdsig'] = df['macd'].ewm(span=7, adjust=False).mean()
    
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(com=5, adjust=False).mean()
    avg_loss = loss.ewm(com=5, adjust=False).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - df['close'].shift()).abs()
    tr3 = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['atr'] = tr.ewm(span=ATR_LEN, adjust=False).mean()
    
    return df

def main():
    exchange = ccxt.okx({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
    try:
        exchange.load_markets()
    except Exception as e:
        print(f"載入市場失敗: {e}")
        return
        
    # 只篩選 USDT 永續合約
    symbols = [s for s in exchange.symbols if 'USDT:USDT' in s and not s.endswith(':USDT-22MAR24') and not s.endswith(':USDT-29JUN24')]
    
    print(f"開始掃描 {len(symbols)} 個永續合約標的...")
    
    for symbol in symbols[:30]:
        try:
            ohlcv_5m = exchange.fetch_ohlcv(symbol, '5m', limit=100)
            ohlcv_15m = exchange.fetch_ohlcv(symbol, '15m', limit=50)
            
            if len(ohlcv_5m) < 50 or len(ohlcv_15m) < 30:
                continue
                
            df_5m = pd.DataFrame(ohlcv_5m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_15m = pd.DataFrame(ohlcv_15m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            df_5m = compute_indicators(df_5m)
            df_15m = compute_indicators(df_15m)
            
            df_15m['htf_bearish'] = df_15m['macd'] < df_15m['macdsig']
            df_5m['datetime'] = pd.to_datetime(df_5m['timestamp'], unit='ms')
            df_15m['datetime'] = pd.to_datetime(df_15m['timestamp'], unit='ms')
            
            df_5m = pd.merge_asof(df_5m, df_15m[['datetime', 'htf_bearish']], on='datetime', direction='backward')
            
            curr = df_5m.iloc[-1]
            prev = df_5m.iloc[-2]
            
            htf_bearish = bool(curr.get('htf_bearish', False))
            fast_ma, slow_ma = curr['ema7'], curr['ema22']
            prev_fast, prev_slow = prev['ema7'], prev['ema22']
            
            crossover_bear = (prev_fast >= prev_slow) and (fast_ma < slow_ma)
            close_price = curr['close']
            macd_bear = curr['macd'] < curr['macdsig']
            rsi_val = curr['rsi']
            
            has_room = (close_price - curr['ema85']) / curr['ema85'] >= MIN_ROOM_PCT and (close_price - curr['ema120']) / curr['ema120'] >= MIN_ROOM_PCT
            not_too_flat = abs(fast_ma - slow_ma) / close_price >= MIN_SPREAD
            price_away = (fast_ma - close_price) / close_price >= MIN_AWAY_PCT
            
            short_signal = htf_bearish and macd_bear and (rsi_val < RSI_LIMIT) and has_room and not_too_flat and price_away and crossover_bear and (close_price < slow_ma)
            
            if short_signal:
                my_atr = curr['atr']
                sl = close_price + (my_atr * SL_MULT)
                tp = close_price - (my_atr * TP_MULT)
                msg = f"🚨 *OKX 智慧短線空單訊號*\n\n標的: `{symbol}`\n價格: `{close_price}`\n停損 (SL): `{sl:.4f}`\n止盈 (TP): `{tp:.4f}`"
                send_telegram_message(msg)
                
            time.sleep(exchange.rateLimit / 1000)
        except Exception as e:
            print(f"處理 {symbol} 時發生錯誤: {e}")

if __name__ == '__main__':
    main()