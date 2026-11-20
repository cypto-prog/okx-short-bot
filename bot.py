def extract_change_pct(ticker):
    """取得 24h 漲跌幅（%），依序嘗試 percentage、open/last 與 OKX info 原始欄位"""
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