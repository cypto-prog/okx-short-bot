import os
import ccxt
import pandas as pd
import requests
from datetime import datetime

# ================= 1. Telegram 通知設定 =================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram 憑證未設定，跳過通知。")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"Telegram 發送失敗: {response.text}")
    except Exception as e:
        print(f"Telegram 連線錯誤: {e}")

# ================= 2. 初始化交易所與市場篩選 =================
exchange = ccxt.okx({
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}  # 預設使用永續合約
})

try:
    exchange.load_markets()
except Exception as e:
    print(f"載入市場失敗: {e}")
    exit(1)

# 篩選出所有 OKX 的 USDT 永續合約 (SWAP)
symbols = []
for symbol, market in exchange.markets.items():
    if market.get('active', False) and market.get('quote') == 'USDT' and market.get('swap', False):
        symbols.append(symbol)

print(f"成功載入 {len(symbols)} 個 OKX USDT 永續合約標的。")

# ================= 3. 策略參數設定 =================
TIMEFRAME_HIGHER = '15m'
TIMEFRAME_LOWER = '5m'
RSI_LIMIT = 75
MIN_ROOM_PCT = 0.006       
MIN_SPREAD = 0.0006        
MIN_AWAY_PCT = 0.0005      
ATR_LEN = 14
SL_MULT = 1.1
TP_MULT = 2.0

# ================= 4. 純 Pandas 指標計算輔助函數 =================
def compute_indicators(df):
    # EMA
    df['ema7'] = df['close'].ewm(span=7, adjust=False).mean()
    df['ema22'] = df['close'].ewm(span=22, adjust=False).mean()
    df['ema85'] = df['close'].ewm(span=85, adjust=False).mean()
    df['ema120'] = df['close'].ewm(span=120, adjust=False).mean()
    
    # MACD (10, 20, 7)
    exp1 = df['close'].ewm(span=10, adjust=False).mean()
    exp2 = df['close'].ewm(span=20, adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['macdsig'] = df['macd'].ewm(span=7, adjust=False).mean()
    
    # RSI (6)
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(com=5, adjust=False).mean()
    avg_loss = loss.ewm(com=5, adjust=False).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # ATR (14)
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - df['close'].shift()).abs()
    tr3 = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['atr'] = tr.ewm(span=ATR_LEN, adjust=False).mean()
    
    return df

# ================= 5. 掃描與運算邏輯 =================
def analyze_market():
    for symbol in symbols:
        try:
            ohlcv_lower = exchange.fetch_ohlcv(symbol, TIMEFRAME_LOWER, limit=100)
            if len(ohlcv_lower) < 50:
                continue
            df_lower = pd.DataFrame(ohlcv_lower, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_lower = compute_indicators(df_lower)
            
            ohlcv_higher = exchange.fetch_ohlcv(symbol, TIMEFRAME_HIGHER, limit=50)
            if len(ohlcv_higher) < 30:
                continue
            df_higher = pd.DataFrame(ohlcv_higher, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_higher = compute_indicators(df_higher)

            c_idx = -2  # 已確認收盤的那根 K 線
            
            htf_bearish = df_higher['macd'].iloc[c_idx] < df_higher['macdsig'].iloc[c_idx]
            
            fast_ma = df_lower['ema7'].iloc[c_idx]
            slow_ma = df_lower['ema22'].iloc[c_idx]
            prev_fast = df_lower['ema7'].iloc[c_idx - 1]
            prev_slow = df_lower['ema22'].iloc[c_idx - 1]
            
            crossover_bear = (prev_fast >= prev_slow) and (fast_ma < slow_ma)
            
            close_price = df_lower['close'].iloc[c_idx]
            macd_bear = df_lower['macd'].iloc[c_idx] < df_lower['macdsig'].iloc[c_idx]
            rsi_val = df_lower['rsi'].iloc[c_idx]
            
            ema85 = df_lower['ema85'].iloc[c_idx]
            ema120 = df_lower['ema120'].iloc[c_idx]
            room_85 = (close_price - ema85) / ema85
            room_120 = (close_price - ema120) / ema120
            has_room = (room_85 >= MIN_ROOM_PCT) and (room_120 >= MIN_ROOM_PCT)
            
            ema_spread = abs(fast_ma - slow_ma) / close_price
            not_too_flat = ema_spread >= MIN_SPREAD
            price_away = (fast_ma - close_price) / close_price >= MIN_AWAY_PCT
            
            rsi_valid = rsi_val < RSI_LIMIT
            price_below = close_price < slow_ma

            base_condition = htf_bearish and macd_bear and rsi_valid and has_room and not_too_flat and price_away
            short_signal = base_condition and crossover_bear and price_below

            if short_signal:
                current_bar_time = df_lower['timestamp'].iloc[c_idx]
                signal_cache_file = f"cache_{symbol.replace('/', '_')}.txt"
                last_sent_time = 0
                if os.path.exists(signal_cache_file):
                    with open(signal_cache_file, "r") as f:
                        last_sent_time = int(f.read().strip() or 0)
                
                if current_bar_time != last_sent_time:
                    my_atr = df_lower['atr'].iloc[c_idx]
                    sl_price = close_price + (my_atr * SL_MULT)
                    tp_price = close_price - (my_atr * TP_MULT)
                    
                    msg = (
                        f"🚨 *[OKX 永續合約空單訊號]* 🚨\n\n"
                        f"◆ 幣種: `{symbol}`\n"
                        f"⏱ 時區組合: 15M 趨勢過濾 + 5M 均線死叉\n"
                        f"💰 建議進場價: `{close_price:.4f}`\n"
                        f"🛑 建議停損 (1.1x ATR): `{sl_price:.4f}`\n"
                        f"🎯 建議止盈 (2.0x ATR): `{tp_price:.4f}`\n"
                        f"⚡ 條件全數吻合，均線已死叉收盤！"
                    )
                    send_telegram_message(msg)
                    print(f"已發送 {symbol} 空單訊號通知。")
                    
                    with open(signal_cache_file, "w") as f:
                        f.write(str(current_bar_time))
                else:
                    print(f"{symbol} 在當前 K 線週期已經發送過訊號，略過重複推播。")

        except Exception as e:
            continue

if __name__ == "__main__":
    print(f"[{datetime.now()}] 開始執行 OKX 永續合約市場掃描...")
    analyze_market()
    print(f"[{datetime.now()}] 掃描結束。")