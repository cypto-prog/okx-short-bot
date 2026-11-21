import json
import os
import re
import time
from datetime import datetime, timezone
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

# 2. 防重複推播設定（同一隻幣種 1 小時內只報一次）
STATE_FILE_PATH = "sent_signals.json"
DEDUP_WINDOW_HOURS = 1
DEDUP_BY_SYMBOL_ONLY = True

# 3. 榜單模式與掃描範圍設定
SCAN_RANK_MODE = "MOVERS"
TOP_N_SYMBOLS = 100
SKIP_TOP_RANK_COUNT = 0
MIN_QUOTE_VOLUME_USDT = 1_000_000

# 4. 排除美股代幣、槓桿 ETF、貴金屬／商品等非加密貨幣延伸商品
ONLY_CRYPTO_PERP = True

EXCLUDED_NON_CRYPTO_BASES = {
    "XAU", "XAG", "XPT", "XPD",
    "WTI", "BRENT", "USOIL", "UKOIL", "NG", "NATGAS",
    "COPPER", "ALUMINUM", "NICKEL", "ZINC", "LEAD",
    "CORN", "WHEAT", "SOYBEAN", "COFFEE", "SUGAR", "COTTON",
    "SPX", "NDX", "DJI", "QQQ", "SPY", "NVDA", "TSLA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "MSTR",
    "SOXS", "SOXL", "TQQQ", "SQQQ", "UVXY", "SPXU", "UPRO",
}

EXCLUDED_NON_CRYPTO_PREFIXES = (
    "X",
)

MIN_DOWNSIDE_SPACE_PCT = 0.8

# 5. 前方插針下探過濾
ENABLE_PRIOR_DIP_FILTER = True
PRIOR_DIP_LOOKBACK = 6
MAX_PRIOR_DIP_BELOW_CLOSE_PCT = 0.8

# 6. 大時區 MACD 即將死叉與防黏設定
ALLOW_PRE_DEAD_CROSS = True
MAX_MACD_GAP_PCT = 0.5
REQUIRE_MACD_TURNING_DOWN = True
MIN_MACD_SPREAD_PCT = 0.05
MACD_CHOP_LOOKBACK = 6
MAX_MACD_CROSS_COUNT = 2

# 7. 防黏均線 / 近期死叉 / 防洗盤過濾
MIN_EMA_SPREAD_PCT = 0.25         # 紫黃均線最小開口 %（提高避免太黏）
MIN_PRICE_BELOW_EMA7_PCT = 0.15   # 收盤價需低於 EMA7 至少此 %（避免 K 棒貼著均線）
MAX_RECENT_CROSS_AGE_BARS = 2     # 死叉需發生在最近幾根已收盤 K 棒內（避免抓舊死叉）
MAX_LOWER_WICK_RATIO = 0.55
COOLDOWN_BARS = 4

# 8. 雙策略開關（只保留 1H+15m 與 15m+5m）
ENABLE_1H_15M = True
ENABLE_15M_5M = True

# 9. ATR 風控設定
ATR_LEN = 14
SL_MULT = 1.15
TP_MULT = 1.6


def make_dedup_key(symbol, strat_label):
    if DEDUP_BY_SYMBOL_ONLY:
        return symbol.strip()
    return f"{symbol.strip()}|{strat_label.strip()}"


def load_sent_history():
    now_ts = time.time()
    max_age_sec = max(0.0, float(DEDUP_WINDOW_HOURS)) * 3600.0
    history = {}

    if os.path.exists(STATE_FILE_PATH):
        try:
            with open(STATE_FILE_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                for key, ts in raw.items():
                    try:
                        ts_val = float(ts)
                        if max_age_sec == 0 or (now_ts - ts_val) <= max_age_sec:
                            history[str(key)] = ts_val
                    except (TypeError, ValueError):
                        continue
        except Exception as e:
            print(f"讀取 {STATE_FILE_PATH} 發生錯誤，使用空紀錄: {e}")

    return history


def save_sent_history(history):
    try:
        with open(STATE_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"寫入 {STATE_FILE_PATH} 發生錯誤: {e}")


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


def extract_base_asset(symbol):
    base_part = symbol.split("/", 1)[0] if "/" in symbol else symbol.split("-", 1)[0]
    return base_part.strip().upper()


def is_crypto_perp_symbol(symbol, market=None, ticker=None):
    if not (symbol.endswith('/USDT:USDT') or (symbol.endswith('USDT') and ':USDT' in symbol)):
        return False

    if not ONLY_CRYPTO_PERP:
        return True

    if market:
        if market.get("swap") is False or market.get("linear") is False:
            return False
        category = str(market.get("category") or "").lower()
        if any(k in category for k in ("stock", "equity", "commodity", "forex", "etf", "rwa")):
            return False

    info = {}
    if isinstance(market, dict) and isinstance(market.get("info"), dict):
        info.update(market["info"])
    if isinstance(ticker, dict) and isinstance(ticker.get("info"), dict):
        info.update(ticker["info"])

    info_text = " ".join(str(v).lower() for v in info.values() if v is not None)
    if any(k in info_text for k in ("stock", "equity", "commodity", "forex", "etf", "rwa", "xstock")):
        return False

    base = extract_base_asset(symbol)
    if not base or not re.fullmatch(r"[A-Z0-9]{2,15}", base):
        return False

    if base in EXCLUDED_NON_CRYPTO_BASES:
        return False

    if base.startswith("X") and len(base) >= 4:
        return False

    for prefix in EXCLUDED_NON_CRYPTO_PREFIXES:
        if prefix != "X" and base.startswith(prefix):
            return False

    return True


def calculate_indicators(df):
    df['EMA_7'] = df['close'].ewm(span=7, adjust=False).mean()
    df['EMA_22'] = df['close'].ewm(span=22, adjust=False).mean()
    df['EMA_85'] = df['close'].ewm(span=85, adjust=False).mean()
    df['EMA_120'] = df['close'].ewm(span=120, adjust=False).mean()

    # MACD (10, 20, 7)
    ema_10 = df['close'].ewm(span=10, adjust=False).mean()
    ema_20 = df['close'].ewm(span=20, adjust=False).mean()
    df['MACD_LINE'] = ema_10 - ema_20
    df['MACD_SIGNAL'] = df['MACD_LINE'].ewm(span=7, adjust=False).mean()
    df['MACD_HIST'] = df['MACD_LINE'] - df['MACD_SIGNAL']

    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()

    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    df['ATR'] = true_range.rolling(window=ATR_LEN).mean()

    return df


def is_bullish_major_trend(bar):
    if bar is None:
        return False
    if pd.isna(bar['EMA_85']) or pd.isna(bar['EMA_120']):
        return False
    return bool(bar['EMA_85'] > bar['EMA_120'])


def get_downside_space_pct(bar):
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
    space_pct = get_downside_space_pct(bar)
    if space_pct is None:
        return False, None
    return bool(space_pct >= min_pct), space_pct


def has_prior_down_wick_dip(closed_df, idx):
    if not ENABLE_PRIOR_DIP_FILTER:
        return False

    curr_bar = closed_df.iloc[idx]
    curr_close = curr_bar['close']
    if pd.isna(curr_close) or curr_close <= 0:
        return False

    start_idx = max(0, idx - max(1, PRIOR_DIP_LOOKBACK) + 1)
    recent_lows = closed_df.iloc[start_idx : idx + 1]['low'].dropna()
    if recent_lows.empty:
        return False

    recent_min_low = float(recent_lows.min())
    dip_below_close_pct = ((curr_close - recent_min_low) / curr_close) * 100.0
    return bool(dip_below_close_pct >= MAX_PRIOR_DIP_BELOW_CLOSE_PCT)


def check_major_macd_dead_cross(df):
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
    2. 死叉發生在最近 MAX_RECENT_CROSS_AGE_BARS 根已收盤 K 棒內（不抓太舊的死叉）
    3. 目前仍處於 EMA_7 < EMA_22，且收盤價明顯低於 EMA_7（不貼均線）
    4. 紫黃均線開口 >= MIN_EMA_SPREAD_PCT
    5. 下影線比例 <= MAX_LOWER_WICK_RATIO，且近期無明顯往下插針
    6. 距離上次符合訊號已達 COOLDOWN_BARS 根 K 棒
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

    last_cross_down_idx = None
    last_signal_idx = None

    for i in range(len(closed_df)):
        if bool(cross_up_series.iloc[i]):
            last_cross_down_idx = None
        if bool(cross_down_series.iloc[i]):
            last_cross_down_idx = i

        bar_i = closed_df.iloc[i]
        if any(pd.isna(bar_i[k]) for k in ['EMA_7', 'EMA_22', 'EMA_85', 'EMA_120', 'close', 'open', 'high', 'low']):
            continue
        if not is_bullish_major_trend(bar_i) or bar_i['close'] <= 0:
            continue

        fresh_cross_ok = (
            last_cross_down_idx is not None
            and ((i - last_cross_down_idx) < max(1, MAX_RECENT_CROSS_AGE_BARS))
        )

        spread_pct = (abs(bar_i['EMA_7'] - bar_i['EMA_22']) / bar_i['close']) * 100.0
        spread_ok = spread_pct >= MIN_EMA_SPREAD_PCT

        below_ema7_pct = ((bar_i['EMA_7'] - bar_i['close']) / bar_i['close']) * 100.0
        not_stuck_ok = (below_ema7_pct >= MIN_PRICE_BELOW_EMA7_PCT) and (bar_i['close'] < bar_i['EMA_22'])

        bar_range = bar_i['high'] - bar_i['low']
        lower_wick = min(bar_i['open'], bar_i['close']) - bar_i['low']
        wick_ratio = (lower_wick / bar_range) if bar_range > 0 else 0.0
        wick_ok = wick_ratio <= MAX_LOWER_WICK_RATIO

        no_prior_dip = not has_prior_down_wick_dip(closed_df, i)

        cond_i = (
            fresh_cross_ok
            and (bar_i['EMA_7'] < bar_i['EMA_22'])
            and not_stuck_ok
            and spread_ok
            and wick_ok
            and no_prior_dip
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


def extract_change_pct(ticker):
    pct = ticker.get("percentage")
    if pct is not None and not pd.isna(pct):
        return float(pct)

    open_p = ticker.get("open")
    last_p = ticker.get("last")
    if open_p and last_p and float(open_p) > 0:
        return ((float(last_p) - float(open_p)) / float(open_p)) * 100.0

    info = ticker.get("info") or {}
    info_last = info.get("last")
    info_open = info.get("open24h") or info.get("sodUtc8") or info.get("sodUtc0")
    if info_open and info_last and float(info_open) > 0:
        return ((float(info_last) - float(info_open)) / float(info_open)) * 100.0

    return None


def scan_market():
    sent_history = load_sent_history()

    exchange = ccxt.okx({
        'enableRateLimit': True,
        'options': {
            'defaultType': 'swap',
        }
    })

    try:
        markets = exchange.load_markets()
        tickers = exchange.fetch_tickers()
    except Exception as e:
        err_msg = f"❌ 取得 OKX 市場資料失敗: {e}"
        print(err_msg)
        send_telegram_message(err_msg)
        return

    mode = SCAN_RANK_MODE.upper()
    candidates = []

    for symbol, ticker in tickers.items():
        market = markets.get(symbol)
        if not is_crypto_perp_symbol(symbol, market=market, ticker=ticker):
            continue

        info = ticker.get("info") or {}
        raw_vol = ticker.get('quoteVolume')
        if raw_vol is None:
            raw_vol = info.get('volCcy24h') or 0.0
        quote_volume = float(raw_vol or 0.0)

        if MIN_QUOTE_VOLUME_USDT > 0 and quote_volume < MIN_QUOTE_VOLUME_USDT:
            continue

        change_pct = extract_change_pct(ticker)
        candidates.append((symbol, quote_volume, float(change_pct) if change_pct is not None else 0.0))

    if mode == "GAINERS":
        candidates.sort(key=lambda x: x[2], reverse=True)
        mode_label = "漲幅榜"
    elif mode == "MOVERS":
        candidates.sort(key=lambda x: abs(x[2]), reverse=True)
        mode_label = "漲跌幅榜"
    else:
        candidates.sort(key=lambda x: x[1], reverse=True)
        mode_label = "成交額榜"

    start_idx = max(0, SKIP_TOP_RANK_COUNT)
    symbols = [item[0] for item in candidates[start_idx : start_idx + TOP_N_SYMBOLS]]

    print(
        f"開始掃描 OKX【{mode_label}】純加密貨幣前 {len(symbols)} 檔"
        f"【雙策略 1H+15m / 15m+5m｜MACD(10,20,7)｜去重 {DEDUP_WINDOW_HOURS}h】..."
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
                dedup_key = make_dedup_key(symbol, strat_label)

                if dedup_key in sent_history:
                    skipped_dup_count += 1
                    print(f"略過 {DEDUP_WINDOW_HOURS} 小時內已報過標的: {symbol} [{strat_label}]")
                    continue

                triggered_count += 1
                stop_loss = close_price + (SL_MULT * atr)
                take_profit = close_price - (TP_MULT * atr)
                now_str = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")

                msg = (
                    f"🚨 OKX 雙策略掃描觸發（{mode_label}・純加密）🚨\n\n"
                    f"📌 標的: {symbol}\n"
                    f"🎯 符合策略: {strat_label}\n"
                    f"📉 訊號邏輯: 大時區 MACD 防黏/即將死叉 + 小時區 EMA 死叉確認\n"
                    f"📊 下方大均線空間: {ref_space_pct:.2f}%\n"
                    f"💰 收盤價: {close_price}\n"
                    f"🛑 建議止損 (SL): {stop_loss:.4f} ({SL_MULT}x ATR)\n"
                    f"🎯 建議指盈 (TP): {take_profit:.4f} ({TP_MULT}x ATR)\n"
                    f"🕒 觸發時間: {now_str}"
                )

                if send_telegram_message(msg):
                    sent_history[dedup_key] = time.time()
                    save_sent_history(sent_history)
                print(f"發現符合標的: {symbol} [{strat_label}] 空間={ref_space_pct:.2f}%")

            time.sleep(0.15)

        except Exception as e:
            error_count += 1
            print(f"處理標的 {symbol} 時發生錯誤: {e}")

    save_sent_history(sent_history)

    print(
        f"掃描完成：掃描 {len(symbols)} 檔、新觸發 {triggered_count} 檔、"
        f"略過重複 {skipped_dup_count} 檔、錯誤 {error_count} 筆。"
    )


if __name__ == "__main__":
    scan_market()