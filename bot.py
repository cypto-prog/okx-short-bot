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


def is_ema7_22_above_85_120(bar):
    """檢查 EMA_7 與 EMA_22 兩條均線是否都在 EMA_85 與 EMA_120 上方"""
    if bar is None:
        return False
    cols = ['EMA_7', 'EMA_22', 'EMA_85', 'EMA_120']
    if any(pd.isna(bar[c]) for c in cols):
        return False
    return bool(
        bar['EMA_7'] > bar['EMA_85']
        and bar['EMA_7'] > bar['EMA_120']
        and bar['EMA_22'] > bar['EMA_85']
        and bar['EMA_22'] > bar['EMA_120']
    )


def get_downside_space_pct(bar):
    """計算剛收盤 K 棒距離下方大級別均線 (EMA_85 / EMA_120) 的下跌空間百分比"""
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


def is_macd_dead_or_imminent(df):
    """判斷剛收盤 K 棒(-2)的 MACD 是否已死叉或即將死叉"""
    if df is None or len(df) < 5:
        return False

    curr = df.iloc[-2]
    prev = df.iloc[-3]

    if pd.isna(curr['MACD_HIST']) or pd.isna(prev['MACD_HIST']):
        return False

    already_dead = curr['MACD_LINE'] < curr['MACD_SIGNAL']
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

    already_dead = curr['EMA_7'] < curr['EMA_22']
    curr_gap = curr['EMA_7'] - curr['EMA_22']
    prev_gap = prev['EMA_7'] - prev['EMA_22']
    gap_pct = abs(curr_gap) / price * 100

    imminent_dead = (
        curr_gap >= 0
        and curr_gap < prev_gap
        and gap_pct <= 0.15
    )

    return bool(already_dead or imminent_dead)


def check_major_tf_setup(df):
    """大時區條件：MACD 即將/已死叉 且 EMA7/22 即將/已死叉 且 EMA7、EMA22 在 EMA85、EMA120 上方"""
    if df is None or len(df) < 5:
        return False, None
    curr = df.iloc[-2]
    ok = (
        is_macd_dead_or_imminent(df)
        and is_ema_dead_or_imminent(df)
        and is_ema7_22_above_85_120(curr)
    )
    return bool(ok), curr


def check_minor_tf_setup(df):
    """
    目標/小週期條件：
    1. EMA7、EMA22 在 EMA85、EMA120 上方
    2. 死叉發生於剛收盤那根(-2) 或 前一根(-3)（即死叉當根或下一根皆可）
    3. 剛收盤 K 棒(-2)收盤價低於 EMA7 與 EMA22（上下影線碰到不限制）
    """
    if df is None or len(df) < 6:
        return False, None

    curr = df.iloc[-2]
    prev = df.iloc[-3]
    prev2 = df.iloc[-4]

    if not is_ema7_22_above_85_120(curr):
        return False, None

    vals = [
        curr['EMA_7'], curr['EMA_22'], curr['close'],
        prev['EMA_7'], prev['EMA_22'],
        prev2['EMA_7'], prev2['EMA_22'],
    ]
    if any(pd.isna(v) for v in vals):
        return False, None

    # 情況 A：剛收盤 K 棒(-2)當根發生死叉
    cross_on_curr = (prev['EMA_7'] >= prev['EMA_22']) and (curr['EMA_7'] < curr['EMA_22'])

    # 情況 B：前一根 K 棒(-3)發生死叉，剛收盤這根(-2)仍維持死叉狀態（下一根收在下面）
    cross_on_prev = (
        (prev2['EMA_7'] >= prev2['EMA_22'])
        and (prev['EMA_7'] < prev['EMA_22'])
        and (curr['EMA_7'] < curr['EMA_22'])
    )

    close_below_cross = (curr['close'] < curr['EMA_7']) and (curr['close'] < curr['EMA_22'])

    return bool((cross_on_curr or cross_on_prev) and close_below_cross), curr


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
    send_telegram_message(
        f"🔔 OKX 三策略掃描器已啟動（前 50 大｜死叉當根或下一根收均線下・不限影線｜下方空間 >= {MIN_DOWNSIDE_SPACE_PCT}%）..."
    )
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
        f"開始掃描 OKX 前 50 大合約【1H+15m / 15m+5m / 15m單獨｜"
        f"死叉當根或下一根收均線下・不限影線｜下方空間 >= {MIN_DOWNSIDE_SPACE_PCT}%】..."
    )

    triggered_count = 0
    skipped_dup_count = 0
    error_count = 0

    for symbol in symbols:
        try:
            df_1h = fetch_tf_df(exchange, symbol, '1h', limit=150)
            df_15m = fetch_tf_df(exchange, symbol, '15m', limit=150)
            df_5m = fetch_tf_df(exchange, symbol, '5m', limit=150)

            major_1h_ok, _ = check_major_tf_setup(df_1h)
            major_15m_ok, _ = check_major_tf_setup(df_15m)

            hit_15m, bar_15m = check_minor_tf_setup(df_15m)
            hit_5m, bar_5m = check_minor_tf_setup(df_5m)

            matched_strategies = []
            ref_bar = None
            ref_space_pct = None

            # 策略 1：1H 配合 15m
            if major_1h_ok and hit_15m and bar_15m is not None:
                space_ok, space_pct = has_enough_downside_space(bar_15m)
                if space_ok:
                    matched_strategies.append("1H+15m")
                    ref_bar = bar_15m
                    ref_space_pct = space_pct

            # 策略 2：15m 配合 5m
            if major_15m_ok and hit_5m and bar_5m is not None:
                space_ok, space_pct = has_enough_downside_space(bar_5m)
                if space_ok:
                    matched_strategies.append("15m+5m")
                    if ref_bar is None:
                        ref_bar = bar_5m
                        ref_space_pct = space_pct

            # 策略 3：15m 單獨
            if major_15m_ok and hit_15m and bar_15m is not None:
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