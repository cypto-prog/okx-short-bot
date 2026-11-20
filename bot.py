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

# 2. 做空空間過濾門檻：收盤價距離下方大級別均線 (EMA_85 / EMA_120) 至少需有幾 % 空間
MIN_DOWNSIDE_SPACE_PCT = 1.2


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
    # 紫線: EMA_7 | 黃線: EMA_22 | 藍線: EMA_85 | 橘線: EMA_120
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


def is_bullish_major_trend(bar):
    """大級別多頭條件：藍線 (EMA_85) 在橘線 (EMA_120) 上方"""
    if bar is None:
        return False
    if pd.isna(bar['EMA_85']) or pd.isna(bar['EMA_120']):
        return False
    return bool(bar['EMA_85'] > bar['EMA_120'])


def get_downside_space_pct(bar):
    """計算剛收盤 K 棒距離下方大級別均線 (藍線 EMA_85 / 橘線 EMA_120) 的下跌空間百分比"""
    if bar is None:
        return None
    price = bar['close']
    ema_85 = bar['EMA_85']
    ema_120 = bar['EMA_120']
    if not price or price <= 0 or pd.isna(ema_85) or pd.isna(ema_120):
        return None

    below_emas = [e for e in (ema_85, ema_120) if e < price]
    if not below_emas:
        return 0.0

    nearest_lower_ema = max(below_emas)
    return (price - nearest_lower_ema) / price * 100


def has_enough_downside_space(bar, min_pct=MIN_DOWNSIDE_SPACE_PCT):
    """做空前提：K 棒距離下方大級別均線還有足夠空間"""
    space_pct = get_downside_space_pct(bar)
    if space_pct is None:
        return False, None
    return bool(space_pct >= min_pct), space_pct


def check_major_macd_dead_cross(df):
    """大時區條件：MACD 死叉（剛收盤 K 棒 MACD_LINE 下穿 MACD_SIGNAL）"""
    if df is None or len(df) < 5:
        return False

    curr = df.iloc[-2]
    prev = df.iloc[-3]

    vals = [
        curr['MACD_LINE'], curr['MACD_SIGNAL'],
        prev['MACD_LINE'], prev['MACD_SIGNAL'],
    ]
    if any(pd.isna(v) for v in vals):
        return False

    return bool(
        (prev['MACD_LINE'] >= prev['MACD_SIGNAL'])
        and (curr['MACD_LINE'] < curr['MACD_SIGNAL'])
    )


def check_minor_ema_dead_cross(df):
    """
    小時區做空條件：
    1. 大級別藍線 (EMA_85) 在橘線 (EMA_120) 上方（多頭背景）
    2. 先確認均線死叉已發生（紫線 EMA_7 下穿黃線 EMA_22，死叉當根或前一根發生皆可）
    3. 死叉後，目前處於死叉狀態且剛收盤 K 棒收在紫線與黃線下方（不限影線）
    """
    if df is None or len(df) < 6:
        return False, None

    curr = df.iloc[-2]
    prev = df.iloc[-3]
    prev2 = df.iloc[-4]

    if not is_bullish_major_trend(curr):
        return False, None

    vals = [
        curr['EMA_7'], curr['EMA_22'], curr['close'],
        prev['EMA_7'], prev['EMA_22'],
        prev2['EMA_7'], prev2['EMA_22'],
    ]
    if any(pd.isna(v) for v in vals):
        return False, None

    # 1. 先確認均線死叉已發生
    dead_cross_confirmed = (
        ((prev['EMA_7'] >= prev['EMA_22']) and (curr['EMA_7'] < curr['EMA_22']))
        or (
            (prev2['EMA_7'] >= prev2['EMA_22'])
            and (prev['EMA_7'] < prev['EMA_22'])
            and (curr['EMA_7'] < curr['EMA_22'])
        )
    )

    # 2. 死叉後 K 棒處在均線下方（目前死叉成立且收盤低於 EMA_7 與 EMA_22）
    after_cross_below_ema = (
        (curr['EMA_7'] < curr['EMA_22'])
        and (curr['close'] < curr['EMA_7'])
        and (curr['close'] < curr['EMA_22'])
    )

    return bool(dead_cross_confirmed and after_cross_below_ema), curr


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
    symbols = [item[0] for item in usdt_swaps[:50]]

    print(
        f"開始掃描 OKX 前 50 大合約【先確認均線死叉 → 死叉後收盤均線下｜"
        f"藍線>橘線 + 大時區MACD死叉｜下方空間 >= {MIN_DOWNSIDE_SPACE_PCT}%】..."
    )

    triggered_count = 0
    skipped_dup_count = 0
    error_count = 0

    for symbol in symbols:
        try:
            df_1h = fetch_tf_df(exchange, symbol, '1h', limit=150)
            df_15m = fetch_tf_df(exchange, symbol, '15m', limit=150)
            df_5m = fetch_tf_df(exchange, symbol, '5m', limit=150)

            macd_dead_1h = check_major_macd_dead_cross(df_1h)
            macd_dead_15m = check_major_macd_dead_cross(df_15m)

            ema_dead_15m, bar_15m = check_minor_ema_dead_cross(df_15m)
            ema_dead_5m, bar_5m = check_minor_ema_dead_cross(df_5m)

            matched_strategies = []
            ref_bar = None
            ref_space_pct = None

            if macd_dead_1h and ema_dead_15m and bar_15m is not None:
                space_ok, space_pct = has_enough_downside_space(bar_15m)
                if space_ok:
                    matched_strategies.append("1H+15m")
                    ref_bar = bar_15m
                    ref_space_pct = space_pct

            if macd_dead_15m and ema_dead_5m and bar_5m is not None:
                space_ok, space_pct = has_enough_downside_space(bar_5m)
                if space_ok:
                    matched_strategies.append("15m+5m")
                    if ref_bar is None:
                        ref_bar = bar_5m
                        ref_space_pct = space_pct

            if macd_dead_15m and ema_dead_15m and bar_15m is not None:
                space_ok, space_pct = has_enough_downside_space(bar_15m)
                if space_ok:
                    matched_strategies.append("15m單獨")
                    if ref_bar is None:
                        ref_bar = bar_15m
                        ref_space_pct = space_pct

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
                    f"📉 訊號邏輯: 均線死叉確認 → 死叉後收盤均線下\n"
                    f"📊 下方大均線空間: {ref_space_pct:.2f}%\n"
                    f"💰 收盤價: {close_price}\n"
                    f"🛑 建議止損 (SL): {stop_loss:.4f} (1.1x ATR)\n"
                    f"🎯 建議指盈 (TP): {take_profit:.4f} (1.6x ATR)"
                )

                if send_telegram_message(msg):
                    sent_signatures.add(signature)
                print(f"發現符合標的: {symbol} [{strat_label}] 空間={ref_space_pct:.2f}%")

            time.sleep(0.15)

        except Exception as e:
            error_count += 1
            print(f"處理標的 {symbol} 時發生錯誤: {e}")

    print(
        f"掃描完成：掃描 {len(symbols)} 檔、新觸發 {triggered_count} 檔、"
        f"略過重複 {skipped_dup_count} 檔、錯誤 {error_count} 筆。"
    )


if __name__ == "__main__":
    scan_market()