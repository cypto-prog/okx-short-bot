
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
    # 均線組
    df['EMA_7'] = df['close'].ewm(span=7, adjust=False).mean()
    df['EMA_22'] = df['close'].ewm(span=22, adjust=False).mean()
    df['EMA_85'] = df['close'].ewm(span=85, adjust=False).mean()
    df['EMA_120'] = df['close'].ewm(span=120, adjust=False).mean()

    # MACD (12, 26, 9)
    ema_12 = df['close'].ewm(span=12, adjust=False).mean()
    ema_26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD_DIF'] = ema_12 - ema_26
    df['MACD_DEA'] = df['MACD_DIF'].ewm(span=9, adjust=False).mean()
    df['MACD_HIST'] = df['MACD_DIF'] - df['MACD_DEA']

    # RSI (14)
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float('nan'))
    df['RSI'] = 100 - (100 / (1 + rs))
    df['RSI'] = df['RSI'].fillna(100)

    # ATR (14)
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    df['ATR'] = true_range.rolling(window=14).mean()

    return df


def is_bearish_base(df):
    """單一時區基礎空方條件：7/22 EMA 死叉或處於死叉、收盤在雙均線下、MACD 偏空"""
    if len(df) < 5:
        return False

    prev_bar = df.iloc[-3]
    curr_bar = df.iloc[-2]

    dead_cross_state = curr_bar['EMA_7'] < curr_bar['EMA_22']
    dead_cross_fresh = (
        (prev_bar['EMA_7'] >= prev_bar['EMA_22'])
        and (curr_bar['EMA_7'] < curr_bar['EMA_22'])
    )

    close_below_ema = (
        (curr_bar['close'] < curr_bar['EMA_7'])
        and (curr_bar['close'] < curr_bar['EMA_22'])
    )

    macd_bearish = (
        (curr_bar['MACD_DIF'] < curr_bar['MACD_DEA'])
        or (curr_bar['MACD_HIST'] < 0)
    )

    return (
        (dead_cross_state or dead_cross_fresh)
        and close_below_ema
        and macd_bearish
    )


def evaluate_strategies(df_5m, df_15m, df_1h):
    """評估三個策略：1) 1h配合15m  2) 15m配合5m  3) 15m獨立"""
    triggered_strategies = []

    bar_15m = df_15m.iloc[-2]
    bar_5m = df_5m.iloc[-2]

    # 策略 1：1h 配合 15m
    if is_bearish_base(df_1h) and is_bearish_base(df_15m):
        triggered_strategies.append(("策略1: 1h配合15m", bar_15m))

    # 策略 2：15m 配合 5m
    if is_bearish_base(df_15m) and is_bearish_base(df_5m):
        triggered_strategies.append(("策略2: 15m配合5m", bar_5m))

    # 策略 3：15m 獨立
    prev_15m = df_15m.iloc[-3]

    dead_cross_15m = (
        (prev_15m['EMA_7'] >= prev_15m['EMA_22'])
        and (bar_15m['EMA_7'] < bar_15m['EMA_22'])
    )

    close_below_15m = (
        (bar_15m['close'] < bar_15m['EMA_7'])
        and (bar_15m['close'] < bar_15m['EMA_22'])
    )

    macd_15m = (
        (bar_15m['MACD_DIF'] < bar_15m['MACD_DEA'])
        or (bar_15m['MACD_HIST'] < 0)
    )

    rsi_ok_15m = bar_15m['RSI'] > 50

    if dead_cross_15m and close_below_15m and macd_15m and rsi_ok_15m:
        triggered_strategies.append(("策略3: 15m獨立", bar_15m))

    return triggered_strategies


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

    tickers = exchange.fetch_tickers()
    usdt_swaps = []

    for symbol, ticker in tickers.items():
        if symbol.endswith('/USDT:USDT') or (
            symbol.endswith('USDT') and ':USDT' in symbol
        ):
            quote_volume = ticker.get('quoteVolume', 0) or 0
            usdt_swaps.append((symbol, quote_volume))

    usdt_swaps.sort(key=lambda x: x[1], reverse=True)
    symbols = [item[0] for item in usdt_swaps[:100]]

    print("開始掃描 OKX 前 100 大合約【1h配合15m / 15m配合5m / 15m獨立 | 均線+MACD+RSI】...")

    triggered_count = 0

    for symbol in symbols:
        try:
            ohlcv_5m = exchange.fetch_ohlcv(
                symbol, timeframe='5m', limit=150
            )
            df_5m = calculate_indicators(
                pd.DataFrame(
                    ohlcv_5m,
                    columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
                )
            )

            ohlcv_15m = exchange.fetch_ohlcv(
                symbol, timeframe='15m', limit=150
            )
            df_15m = calculate_indicators(
                pd.DataFrame(
                    ohlcv_15m,
                    columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
                )
            )

            ohlcv_1h = exchange.fetch_ohlcv(
                symbol, timeframe='1h', limit=150
            )
            df_1h = calculate_indicators(
                pd.DataFrame(
                    ohlcv_1h,
                    columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
                )
            )

            hits = evaluate_strategies(df_5m, df_15m, df_1h)

            if hits:
                triggered_count += 1

                strategy_names = ", ".join(
                    [name for name, _ in hits]
                )

                ref_bar = hits[0][1]
                close_price = ref_bar['close']
                atr = ref_bar['ATR']

                stop_loss = close_price + (1.1 * atr)
                take_profit = close_price - (1.6 * atr)

                msg = (
                    f"🚨 **OKX 空方訊號觸發** 🚨\n\n"
                    f"📌 **標的**: `{symbol}`\n"
                    f"🧭 **符合策略**: `{strategy_names}`\n"
                    f"📈 **15m RSI**: `{df_15m.iloc[-2]['RSI']:.1f}`\n"
                    f"💰 **參考收盤價**: `{close_price}`\n"
                    f"🛑 **建議止損 (SL)**: `{stop_loss:.4f}` (1.1x ATR)\n"
                    f"🎯 **建議指盈 (TP)**: `{take_profit:.4f}` (1.6x ATR)"
                )

                send_telegram_message(msg)
                print(f"發現符合標的: {symbol} [{strategy_names}]")

        except Exception as e:
            print(f"處理標的 {symbol} 時發生錯誤: {e}")

    print(f"掃描完成，本輪共觸發 {triggered_count} 檔符合條件標的。")


if __name__ == "__main__":
    scan_market()
