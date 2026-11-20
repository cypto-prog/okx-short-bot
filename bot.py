import os
import time
import ccxt
import pandas as pd
import requests

# 1. 取得 Telegram 設定（優先讀取 GitHub Secrets / 環境變數）
TELEGRAM_BOT_TOKEN = (
    os.environ.get("TELEGRAM_BOT_TOKEN")
    or "9295995564:AAH8PkxghF_ngjZwMVpLUmMTpJ9rVx0EtYY"
)
TELEGRAM_CHAT_ID = (
    os.environ.get("TELEGRAM_CHAT_ID")
    or "7686655302"
)

# 2. 下方大均線空間設定
MIN_DOWNSIDE_SPACE_PCT = 1.2

# 3. 大時區 MACD 即將死叉與防黏設定
ALLOW_PRE_DEAD_CROSS = True
MAX_MACD_GAP_PCT = 0.4
REQUIRE_MACD_TURNING_DOWN = True
MIN_MACD_SPREAD_PCT = 0.08
MACD_CHOP_LOOKBACK = 6
MAX_MACD_CROSS_COUNT = 1

# 4. 防黏均線 / 防洗盤過濾
MIN_EMA_SPREAD_PCT = 0.15
MAX_LOWER_WICK_RATIO = 0.45
COOLDOWN_BARS = 6

# 5. 雙策略開關（只保留 1H+15m 與 15m+5m）
ENABLE_1H_15M = True
ENABLE_15M_5M = True

# 6. ATR 風控設定
ATR_LEN = 14
SL_MULT = 1.15
TP_MULT = 1.6


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
            if "OKX 雙策略掃描觸發" not in text and "OKX 三策略掃描觸發" not in text:
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

    # ATR
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()

    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    df['ATR'] = true_range.rolling(window=ATR_LEN).mean()

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
    """
    大時區 MACD 條件：
    1. 已死叉 或 即將死叉（準備交叉）
    2. 快慢線差距 >= MIN_MACD_SPREAD_PCT（避免太黏）
    3. 近 MACD_CHOP_LOOKBACK 根 K 棒內交叉次數 <= MAX_MACD_CROSS_COUNT（避免反覆糾纏）
    """
    if df is None or len(df) < max(MACD_CHOP_LOOKBACK + 4, 8):
        return False

    curr = df.iloc[-2]
    prev = df.iloc[-3]

    vals = [
        curr['MACD_LINE'], curr['MACD_SIGNAL'], curr['close'],
        prev['MACD_LINE'], prev['MACD_SIGNAL'],
    ]
    if any(pd.isna(v) for v in vals) or not curr['close'] or curr['close'] <= 0:
        return False

    # 計算近期交叉次數（排除最新未收盤 -1，以已收盤序列計算）
    closed_df = df.iloc[:-1].copy()
    m_line = closed_df['MACD_LINE']
    s_line = closed_df['MACD_SIGNAL']
    m_prev = m_line.shift(1)
    s_prev = s_line.shift(1)

    dead_cross_series = (m_prev >= s_prev) & (m_line < s_line)
    gold_cross_series = (m_prev <= s_prev) & (m_line > s_line)
    any_cross_series = (dead_cross_series | gold_cross_series).astype(int)

    recent_cross_count = int(any_cross_series.tail(MACD_CHOP_LOOKBACK).sum())
    not_chopping = recent_cross_count <= MAX_MACD_CROSS_COUNT

    abs_gap_pct = (abs(curr['MACD_LINE'] - curr['MACD_SIGNAL']) / curr['close']) * 100.0
    spread_ok = abs_gap_pct >= MIN_MACD_SPREAD_PCT

    already_dead_cross = bool(dead_cross_series.iloc[-1])

    pre_dead_cross = False
    if ALLOW_PRE_DEAD_CROSS:
        above_signal = curr['MACD_LINE'] > curr['MACD_SIGNAL']
        gap_pct = ((curr['MACD_LINE'] - curr['MACD_SIGNAL']) / curr['close']) * 100.0
        close_enough = (0.0 <= gap_pct <= MAX_MACD_GAP_PCT)
        turning_down = (not REQUIRE_MACD_TURNING_DOWN) or (curr['MACD_LINE'] < prev['MACD_LINE'])
        pre_dead_cross = bool(above_signal and close_enough and turning_down)

    return bool((already_dead_cross or pre_dead_cross) and spread_ok and not_chopping)


def check_minor_ema_dead_cross(df):
    """
    小時區做空條件：
    1. 大級別藍線 (EMA_85) 在橘線 (EMA_120) 上方（多頭背景）
    2. 紫線 (EMA_7) 下穿黃線 (EMA_22) 死叉確認，且目前仍處於死叉狀態並收在兩條均線下方
    3. 紫黃均線開口 >= MIN_EMA_SPREAD_PCT（避免黏在一起）
    4. 下影線比例 <= MAX_LOWER_WICK_RATIO（避免洗盤下影針）
    5. 距離上次符合訊號已達 COOLDOWN_BARS 根 K 棒
    """
    if df is None or len(df) < 10:
        return False, None

    closed_df = df.iloc[:-1].reset_index(drop=True)
    curr = closed_df.iloc[-1]

    if not is_bullish_major_trend(curr):
        return False, None

    vals = [curr['EMA_7'], curr['EMA_22'], curr['close'], curr['open'], curr['high'], curr['low']]
    if any(pd.isna(v) for v in vals) or not curr['close'] or curr['close'] <= 0:
        return False, None

    e7 = closed_df['EMA_7']
    e22 = closed_df['EMA_22']
    e7_prev = e7.shift(1)
    e22_prev = e22.shift(1)

    cross_down_series = (e7_prev >= e22_prev) & (e7 < e22)
    cross_up_series = (e7_prev <= e22_prev) & (e7 > e22)

    has_crossed_down = False
    last_signal_idx = None

    for i in range(len(closed_df)):
        if bool(cross_up_series.iloc[i]):
            has_crossed_down = False
        if bool(cross_down_series.iloc[i]):
            has_crossed_down = True

        bar_i = closed_df.iloc[i]
        if any(pd.isna(bar_i[k]) for k in ['EMA_7', 'EMA_22', 'EMA_85', 'EMA_120', 'close', 'open', 'high', 'low']):
            continue
        if not is_bullish_major_trend(bar_i) or bar_i['close'] <= 0:
            continue

        spread_pct = (abs(bar_i['EMA_7'] - bar_i['EMA_22']) / bar_i['close']) * 100.0
        spread_ok = spread_pct >= MIN_EMA_SPREAD_PCT

        bar_range = bar_i['high'] - bar_i['low']
        lower_wick = min(bar_i['open'], bar_i['close']) - bar_i['low']
        wick_ratio = (lower_wick / bar_range) if bar_range > 0 else 0.0
        wick_ok = wick_ratio <= MAX_LOWER_WICK_RATIO

        cond_i = (
            has_crossed_down
            and (bar_i['EMA_7'] < bar_i['EMA_22'])
            and (bar_i['close'] < bar_i['EMA_7'])
            and (bar_i['close'] < bar_i['EMA_22'])
            and spread_ok
            and wick_ok
        )

        cooldown_ok = (last_signal_idx is None) or ((i - last_signal_idx) >= COOLDOWN_BARS)

        if cond_i and cooldown_ok:
            last_signal_idx = i
            if i == len(closed_df) - 1:
                return True, curr

    return False, None


def fetch_tf_df(exchange, symbol, timeframe, limit=180):
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
        f"開始掃描 OKX 前 50 大合約【雙策略 1H+15m / 15m+5m｜"
        f"下方空間 >= {MIN_DOWNSIDE_SPACE_PCT}%｜冷卻 {COOLDOWN_BARS} 根】..."
    )

    triggered_count = 0
    skipped_dup_count = 0
    error_count = 0

    for symbol in symbols:
        try:
            df_1h = fetch_tf_df(exchange, symbol, '1h', limit=180)
            df_15m = fetch_tf_df(exchange, symbol, '15m', limit=180)
            df_5m = fetch_tf_df(exchange, symbol, '5m', limit=180)

            macd_ready_1h = check_major_macd_dead_cross(df_1h) if ENABLE_1H_15M else False
            macd_ready_15m = check_major_macd_dead_cross(df_15m) if ENABLE_15M_5M else False

            ema_dead_15m, bar_15m = check_minor_ema_dead_cross(df_15m)
            ema_dead_5m, bar_5m = check_minor_ema_dead_cross(df_5m)

            matched_strategies = []
            ref_bar = None
            ref_space_pct = None

            if ENABLE_1H_15M and macd_ready_1h and ema_dead_15m and bar_15m is not None:
                space_ok, space_pct = has_enough_downside_space(bar_15m)
                if space_ok:
                    matched_strategies.append("1H+15m")
                    ref_bar = bar_15m
                    ref_space_pct = space_pct

            if ENABLE_15M_5M and macd_ready_15m and ema_dead_5m and bar_5m is not None:
                space_ok, space_pct = has_enough_downside_space(bar_5m)
                if space_ok:
                    matched_strategies.append("15m+5m")
                    if ref_bar is None:
                        ref_bar = bar_5m
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
                stop_loss = close_price + (SL_MULT * atr)
                take_profit = close_price - (TP_MULT * atr)

                msg = (
                    f"🚨 OKX 雙策略掃描觸發 🚨\n\n"
                    f"📌 標的: {symbol}\n"
                    f"🎯 符合策略: {strat_label}\n"
                    f"📉 訊號邏輯: 大時區 MACD 防黏/即將死叉 + 小時區 EMA 死叉確認\n"
                    f"📊 下方大均線空間: {ref_space_pct:.2f}%\n"
                    f"💰 收盤價: {close_price}\n"
                    f"🛑 建議止損 (SL): {stop_loss:.4f} ({SL_MULT}x ATR)\n"
                    f"🎯 建議指盈 (TP): {take_profit:.4f} ({TP_MULT}x ATR)"
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