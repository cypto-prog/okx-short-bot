import os
import ccxt
import pandas as pd
import requests

# 1. 取得 Telegram 設定（優先讀取 Secrets，若無則使用最新重設之 Token）
TELEGRAM_BOT_TOKEN = (
    os.environ.get("TELEGRAM_BOT_TOKEN")
    or "9162800441:AAEERg3azJje9icu12UQUTzhFiAcnE4ljg"
)
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# RSI 設定
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70


def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram 密鑰或 CHAT_ID 未設定，略過發送。")
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

    # RSI 計算
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.ewm(
        alpha=1 / RSI_PERIOD,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / RSI_PERIOD,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, float('nan'))
    df['RSI'] = 100 - (100 / (1 + rs))
    df['RSI'] = df['RSI'].fillna(100)

    # ATR 計算
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()

    ranges = pd.concat(
        [high_low, high_close, low_close],
        axis=1
    )

    true_range = ranges.max(axis=1)
    df['ATR'] = true_range.rolling(window=14).mean()

    return df


def check_timeframe_signal(df):
    """檢查單一時區剛收盤 K 棒是否符合做空條件"""

    if len(df) < 5:
        return False, None

    prev_bar = df.iloc[-3]
    curr_bar = df.iloc[-2]

    # 1. EMA7 下穿 EMA22
    dead_cross = (
        prev_bar['EMA_7'] >= prev_bar['EMA_22']
        and curr_bar['EMA_7'] < curr_bar['EMA_22']
    )

    # 2. 收盤價低於 EMA7 和 EMA22
    close_below_ema = (
        curr_bar['close'] < curr_bar['EMA_7']
        and curr_bar['close'] < curr_bar['EMA_22']
    )

    # 3. RSI 超買
    rsi_overbought = curr_bar['RSI'] > RSI_OVERBOUGHT

    triggered = (
        dead_cross
        and close_below_ema
        and rsi_overbought
    )

    return triggered, curr_bar


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

    # 取得成交量前 100 大 USDT 永續合約
    try:
        tickers = exchange.fetch_tickers()

    except Exception as e:
        print(f"取得市場行情失敗: {e}")
        return

    usdt_swaps = []

    for symbol, ticker in tickers.items():

        if symbol.endswith('/USDT:USDT') or (
            symbol.endswith('USDT') and ':USDT' in symbol
        ):
            quote_volume = ticker.get('quoteVolume', 0) or 0
            usdt_swaps.append((symbol, quote_volume))

    usdt_swaps.sort(
        key=lambda x: x[1],
        reverse=True
    )

    symbols = [
        item[0] for item in usdt_swaps[:100]
    ]

    print(
        f"開始掃描 OKX 前 100 大合約 "
        f"【5m / 15m / 1h：死叉 + 收盤在均線下 + RSI>{RSI_OVERBOUGHT}】..."
    )

    triggered_count = 0

    for symbol in symbols:

        try:
            # 取得三個時間週期資料
            ohlcv_5m = exchange.fetch_ohlcv(
                symbol,
                timeframe='5m',
                limit=150
            )

            df_5m = calculate_indicators(
                pd.DataFrame(
                    ohlcv_5m,
                    columns=[
                        'timestamp',
                        'open',
                        'high',
                        'low',
                        'close',
                        'volume'
                    ]
                )
            )

            ohlcv_15m = exchange.fetch_ohlcv(
                symbol,
                timeframe='15m',
                limit=150
            )

            df_15m = calculate_indicators(
                pd.DataFrame(
                    ohlcv_15m,
                    columns=[
                        'timestamp',
                        'open',
                        'high',
                        'low',
                        'close',
                        'volume'
                    ]
                )
            )

            ohlcv_1h = exchange.fetch_ohlcv(
                symbol,
                timeframe='1h',
                limit=150
            )

            df_1h = calculate_indicators(
                pd.DataFrame(
                    ohlcv_1h,
                    columns=[
                        'timestamp',
                        'open',
                        'high',
                        'low',
                        'close',
                        'volume'
                    ]
                )
            )

            # 三個週期獨立判斷
            hit_5m, bar_5m = check_timeframe_signal(df_5m)
            hit_15m, bar_15m = check_timeframe_signal(df_15m)
            hit_1h, bar_1h = check_timeframe_signal(df_1h)

            matched_tfs = []
            ref_bar = None
            rsi_details = []

            if hit_5m:
                matched_tfs.append("5m")
                ref_bar = bar_5m
                rsi_details.append(
                    f"5m RSI: {bar_5m['RSI']:.1f}"
                )

            if hit_15m:
                matched_tfs.append("15m")

                if ref_bar is None:
                    ref_bar = bar_15m

                rsi_details.append(
                    f"15m RSI: {bar_15m['RSI']:.1f}"
                )

            if hit_1h:
                matched_tfs.append("1h")

                if ref_bar is None:
                    ref_bar = bar_1h

                rsi_details.append(
                    f"1h RSI: {bar_1h['RSI']:.1f}"
                )

            # 有符合條件才發送通知
            if matched_tfs and ref_bar is not None:

                triggered_count += 1

                close_price = ref_bar['close']
                atr = ref_bar['ATR']

                if pd.isna(atr):
                    print(f"{symbol} ATR 資料不足，略過風控價格計算。")
                    continue

                stop_loss = close_price + (1.1 * atr)
                take_profit = close_price - (1.6 * atr)

                msg = (
                    f"🚨 **OKX 死叉+收盤均線下+RSI超買 觸發** 🚨\n\n"
                    f"📌 **標的**: `{symbol}`\n"
                    f"⏰ **觸發時區**: `{', '.join(matched_tfs)}`\n"
                    f"📊 **超買數值**: `{', '.join(rsi_details)}`\n"
                    f"💰 **收盤價**: `{close_price}`\n"
                    f"🛑 **建議止損 (SL)**: `{stop_loss:.4f}` (1.1x ATR)\n"
                    f"🎯 **建議指盈 (TP)**: `{take_profit:.4f}` (1.6x ATR)"
                )

                send_telegram_message(msg)

                print(
                    f"發現符合標的: {symbol} "
                    f"[{', '.join(matched_tfs)}] "
                    f"({', '.join(rsi_details)})"
                )

        except Exception as e:
            print(f"處理標的 {symbol} 時發生錯誤: {e}")

    print(
        f"掃描完成，本輪共觸發 "
        f"{triggered_count} 檔符合條件標的。"
    )


if __name__ == "__main__":
    scan_market()