import os
import time
import ccxt
import pandas as pd
import requests

# 1. 取得 Telegram 設定
TELEGRAM_BOT_TOKEN = (
    os.environ.get("TELEGRAM_BOT_TOKEN")
    or "9295995564:AAH8PkxghF_ngjZwMVpLUmMTpJ9rVx0EtYY"
)
TELEGRAM_CHAT_ID = (
    os.environ.get("TELEGRAM_CHAT_ID")
    or "7686655302"
)


def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram 密鑰未設定，略過發送。")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("Telegram 通知發送成功！")
            return True
        else:
            print(f"Telegram 發送失敗: {response.text}")
            return False
    except Exception as e:
        print(f"發送 Telegram 發生錯誤: {e}")
        return False


def get_recent_sent_signatures(limit=100):
    """從近期 Telegram 訊息紀錄讀取已發送過的訊號簽章，避免重複推播"""
    seen = set()
    if not TELEGRAM_BOT_TOKEN:
        return seen

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"limit": limit}

    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            return seen

        data = response.json()
        for item in data.get("result", []):
            msg_obj = (
                item.get("message")
                or item.get("channel_post")
                or {}
            )
            chat_id = str(msg_obj.get("chat", {}).get("id", ""))
            if chat_id and chat_id != str(TELEGRAM_CHAT_ID):
                continue

            text = msg_obj.get("text", "")
            if "OKX 死叉+收盤均線下 觸發" not in text:
                continue

            symbol_line = ""
            tf_line = ""
            for line in text.splitlines():
                if "標的:" in line:
                    symbol_line = line.split("標的:", 1)[1].strip()
                elif "觸發時區:" in line:
                    tf_line = line.split("觸發時區:", 1)[1].strip()

            if symbol_line and tf_line:
                seen.add(f"{symbol_line}|{tf_line}")

    except Exception as e:
        print(f"讀取歷史訊號紀錄時略過錯誤: {e}")

    return seen


def calculate_indicators(df):
    df['EMA_7'] = df['close'].ewm(span=7, adjust=False).mean()
    df['EMA_22'] = df['close'].ewm(span=22, adjust=False).mean()
    df['EMA_85'] = df['close'].ewm(span=85, adjust=False).mean()
    df['EMA_120'] = df['close'].ewm(span=120, adjust=False).mean()

    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()

    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    df['ATR'] = true_range.rolling(window=14).mean()

    return df


def check_timeframe_signal(df):
    """檢查單一時區剛收盤 K 棒：處於死叉狀態 + 收盤價低於 EMA7 與 EMA22"""
    if df is None or len(df) < 5:
        return False, None

    curr_bar = df.iloc[-2]

    in_dead_cross = curr_bar['EMA_7'] < curr_bar['EMA_22']
    close_below_ema = (
        curr_bar['close'] < curr_bar['EMA_7']
        and curr_bar['close'] < curr_bar['EMA_22']
    )

    triggered = bool(in_dead_cross and close_below_ema)
    return triggered, curr_bar


def fetch_tf_df(exchange, symbol, timeframe, limit=150):
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    if not ohlcv:
        return None
    df = pd.DataFrame(
        ohlcv,
        columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
    )
    return calculate_indicators(df)


def scan_market():
    send_telegram_message("🔔 OKX 掃描器已啟動，開始檢查前 100 大 USDT 永續合約...")
    sent_signatures = get_recent_sent_signatures(limit=100)

    exchange = ccxt.okx({
        'enableRateLimit': True,
        'options': {
            'defaultType': 'swap',
        }
    })

    try:
        exchange.load_markets()
        tickers = exchange.fetch_tickers()
    except Exception as e:
        err_msg = f"❌ 取得 OKX 市場資料失敗: {e}"
        print(err_msg)
        send_telegram_message(err_msg)
        return

    usdt_swaps = []
    for symbol, ticker in tickers.items():
        if symbol.endswith('/USDT:USDT') or (
            symbol.endswith('USDT') and ':USDT' in symbol
        ):
            quote_volume = ticker.get('quoteVolume', 0) or 0
            usdt_swaps.append((symbol, quote_volume))

    usdt_swaps.sort(key=lambda x: x[1], reverse=True)
    symbols = [item[0] for item in usdt_swaps[:100]]

    print("開始掃描 OKX 前 100 大合約【5m / 15m / 1h：死叉狀態 + 收盤在均線下】...")

    triggered_count = 0
    skipped_dup_count = 0
    error_count = 0

    for symbol in symbols:
        try:
            df_5m = fetch_tf_df(exchange, symbol, '5m', limit=150)
            df_15m = fetch_tf_df(exchange, symbol, '15m', limit=150)
            df_1h = fetch_tf_df(exchange, symbol, '1h', limit=150)

            hit_5m, bar_5m = check_timeframe_signal(df_5m)
            hit_15m, bar_15m = check_timeframe_signal(df_15m)
            hit_1h, bar_1h = check_timeframe_signal(df_1h)

            matched_tfs = []
            ref_bar = None

            if hit_5m:
                matched_tfs.append("5m")
                ref_bar = bar_5m

            if hit_15m:
                matched_tfs.append("15m")
                if ref_bar is None:
                    ref_bar = bar_15m

            if hit_1h:
                matched_tfs.append("1h")
                if ref_bar is None:
                    ref_bar = bar_1h

            if matched_tfs and ref_bar is not None:
                close_price = ref_bar['close']
                atr = ref_bar['ATR']

                if pd.isna(atr):
                    print(f"{symbol} ATR 資料不足，略過風控價格計算。")
                    continue

                tf_label = ', '.join(matched_tfs)
                signature = f"{symbol}|{tf_label}"

                if signature in sent_signatures:
                    skipped_dup_count += 1
                    print(f"略過重複訊號: {symbol} [{tf_label}]")
                    continue

                triggered_count += 1
                stop_loss = close_price + (1.1 * atr)
                take_profit = close_price - (1.6 * atr)

                msg = (
                    f"🚨 OKX 死叉+收盤均線下 觸發 🚨\n\n"
                    f"📌 標的: {symbol}\n"
                    f"⏰ 觸發時區: {tf_label}\n"
                    f"💰 收盤價: {close_price}\n"
                    f"🛑 建議止損 (SL): {stop_loss:.4f} (1.1x ATR)\n"
                    f"🎯 建議指盈 (TP): {take_profit:.4f} (1.6x ATR)"
                )

                if send_telegram_message(msg):
                    sent_signatures.add(signature)
                print(f"發現符合標的: {symbol} [{tf_label}]")

            time.sleep(0.15)

        except Exception as e:
            error_count += 1
            print(f"處理標的 {symbol} 時發生錯誤: {e}")

    summary = (
        f"✅ 本輪掃描完成\n"
        f"• 掃描標的數: {len(symbols)}\n"
        f"• 新觸發通知數: {triggered_count}\n"
        f"• 略過重複數: {skipped_dup_count}\n"
        f"• 錯誤筆數: {error_count}"
    )
    print(summary)
    send_telegram_message(summary)


if __name__ == "__main__":
    scan_market()
    