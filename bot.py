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
            if "OKX 三策略掃描觸發" not in text:
                continue

            symbol_line = ""
            strat_line = ""
            for line in text.splitlines():
                if "標的:" in line:
                    symbol_line = line.split("標的:", 1)[1].strip()
                elif "符合策略:" in line:
                    strat_line = line.split("符合策略:", 1)[1].strip()

            if symbol_line and strat_line:
                seen.add(f"{symbol_line}|{strat_line}")

    except Exception as e:
        print(f"讀取歷史訊號紀錄時略過錯誤: {e}")

    return seen


def calculate_indicators(df):
    df['EMA_7'] = df['close'].ewm(span=7, adjust=False).mean()
    df['EMA_22'] = df['close'].ewm(span=22, adjust=False).mean()
    df['EMA_85'] = df['close'].ewm(span=85, adjust=False).mean()
    df['EMA_120'] = df['close'].ewm(span=120, adjust=False).mean()

    # MACD (12, 26, 9)
    ema_12 = df['close'].ewm(span=12, adjust=False).mean()
    ema_26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD_LINE'] = ema_12 - ema_26
    df['MACD_SIGNAL'] = df['MACD_LINE'].ewm(span=9, adjust=False).mean()
    df['MACD_HIST'] = df['MACD_LINE'] - df['MACD_SIGNAL']

    # ATR (14)
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()

    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    df['ATR'] = true_range.rolling(window=14).mean()

    return df


def is_macd_dead_or_imminent(df):
    """判斷剛收盤 K 棒(-2)的 MACD 是否已死叉或即將死叉"""
    if df is None or len(df) < 5:
        return False

    curr = df.iloc[-2]
    prev = df.iloc[-3]

    if pd.isna(curr['MACD_HIST']) or pd.isna(prev['MACD_HIST']):
        return False

    # 1. 已死叉：MACD 線低於 Signal 線（柱狀圖 < 0）
    already_dead = curr['MACD_LINE'] < curr['MACD_SIGNAL']

    # 2. 即將死叉：柱狀圖仍在正值但快速收斂（小於前一根 50% 且柱狀圖下降）
    imminent_dead = (
        curr['MACD_HIST'] > 0
        and prev['MACD_HIST'] > 0
        and curr['MACD_HIST'] < prev['MACD_HIST'] * 0.5
    )

    return bool(already_dead or imminent_dead)


def is_ema_dead_or_imminent(df):
    """判斷剛收盤 K 棒(-2)的 EMA7 / EMA22 是否已死叉或即將死叉"""
    if df is None or len(df) < 5:
        return False

    curr = df.iloc[-2]
    prev = df.iloc[-3]
    price = curr['close']
    if not price or price <= 0:
        return False

    # 1. 已死叉：EMA7 < EMA22
    already_dead = curr['EMA_7'] < curr['EMA_22']

    # 2. 即將死叉：EMA7 仍在 EMA22 之上或貼近，但差距縮小且距離收斂在 0.15% 內
    curr_gap = curr['EMA_7'] - curr['EMA_22']
    prev_gap = prev['EMA_7'] - prev['EMA_22']
    gap_pct = abs(curr_gap) / price * 100

    imminent_dead = (
        curr_gap >= 0
        and curr_gap < prev_gap
        and gap_pct <= 0.15
    )

    return bool(already_dead or imminent_dead)


def check_dead_cross_below_ema(df):
    """檢查小週期剛收盤 K 棒：處於死叉狀態 (EMA7 < EMA22) 且收盤低於 EMA7 與 EMA22"""
    if df is None or len(df) < 5:
        return False, None

    curr_bar = df.iloc[-2]
    in_dead_cross = curr_bar['EMA_7'] < curr_bar['EMA_22']
    close_below_ema = (
        curr_bar['close'] < curr_bar['EMA_7']
        and curr_bar['close'] < curr_bar['EMA_22']
    )
    return bool(in_dead_cross and close_below_ema), curr_bar


def check_major_tf_bearish_setup(df):
    """大時區嚴格條件：MACD 即將/已死叉 且 EMA7/22 即將/已死叉"""
    if df is None or len(df) < 5:
        return False
    return bool(is_macd_dead_or_imminent(df) and is_ema_dead_or_imminent(df))


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
    send_telegram_message("🔔 OKX 三策略（大時區雙死叉/準死叉加嚴版）掃描器已啟動...")
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

    print("開始掃描 OKX 前 100 大合約【大時區 MACD+均線 即將/已死叉 加嚴版】...")

    triggered_count = 0
    skipped_dup_count = 0
    error_count = 0

    for symbol in symbols:
        try:
            df_1h = fetch_tf_df(exchange, symbol, '1h', limit=150)
            df_15m = fetch_tf_df(exchange, symbol, '15m', limit=150)
            df_5m = fetch_tf_df(exchange, symbol, '5m', limit=150)

            major_1h_ok = check_major_tf_bearish_setup(df_1h)
            major_15m_ok = check_major_tf_bearish_setup(df_15m)

            hit_15m, bar_15m = check_dead_cross_below_ema(df_15m)
            hit_5m, bar_5m = check_dead_cross_below_ema(df_5m)

            matched_strategies = []
            ref_bar = None

            # 策略 1：1H 配合 15m（1H 大時區 MACD + 均線 即將/已死叉）
            if major_1h_ok and hit_15m and bar_15m is not None:
                matched_strategies.append("1H+15m")
                ref_bar = bar_15m

            # 策略 2：15m 配合 5m（15m 大時區 MACD + 均線 即將/已死叉）
            if major_15m_ok and hit_5m and bar_5m is not None:
                matched_strategies.append("15m+5m")
                if ref_bar is None:
                    ref_bar = bar_5m

            # 策略 3：15m 單獨（15m MACD + 均線 即將/已死叉 且收盤低於 EMA7/22）
            if major_15m_ok and hit_15m and bar_15m is not None:
                matched_strategies.append("15m單獨")
                if ref_bar is None:
                    ref_bar = bar_15m

            if matched_strategies and ref_bar is not None:
                close_price = ref_bar['close']
                atr = ref_bar['ATR']

                if pd.isna(atr):
                    print(f"{symbol} ATR 資料不足，略過風控價格計算。")
                    continue

                strat_label = ', '.join(matched_strategies)
                signature = f"{symbol}|{strat_label}"

                if signature in sent_signatures:
                    skipped_dup_count += 1
                    print(f"略過重複訊號: {symbol} [{strat_label}]")
                    continue

                triggered_count += 1
                stop_loss = close_price + (1.1 * atr)
                take_profit = close_price - (1.6 * atr)

                msg = (
                    f"🚨 OKX 三策略掃描觸發 🚨\n\n"
                    f"📌 標的: {symbol}\n"
                    f"🎯 符合策略: {strat_label}\n"
                    f"💰 收盤價: {close_price}\n"
                    f"🛑 建議止損 (SL): {stop_loss:.4f} (1.1x ATR)\n"
                    f"🎯 建議指盈 (TP): {take_profit:.4f} (1.6x ATR)"
                )

                if send_telegram_message(msg):
                    sent_signatures.add(signature)
                print(f"發現符合標的: {symbol} [{strat_label}]")

            time.sleep(0.15)

        except Exception as e:
            error_count += 1
            print(f"處理標的 {symbol} 時發生錯誤: {e}")

    summary = (
        f"✅ 本輪三策略掃描完成\n"
        f"• 掃描標的數: {len(symbols)}\n"
        f"• 新觸發通知數: {triggered_count}\n"
        f"• 略過重複數: {skipped_dup_count}\n"
        f"• 錯誤筆數: {error_count}"
    )
    print(summary)
    send_telegram_message(summary)


if __name__ == "__main__":
    scan_market()
    