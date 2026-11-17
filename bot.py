//@version=6
indicator("雙週期 + RSI超買過熱確認 智慧短線空單策略", overlay=true)

// ===== 參數設定 =====
mode = input.string("模式 A：15m + 5m", "週期模式", options=["模式 A：15m + 5m", "模式 B：1H + 15m"])

rsiOverbought = input.float(60.0, "RSI6 超買過熱門檻 (>= 60)")

emaLongLen1 = input.int(85, "大級別長均線 1 (EMA 85)")
emaLongLen2 = input.int(120, "大級別長均線 2 (EMA 120)")
minRoomPct  = input.float(0.4, "最小下行空間百分比 (%)") / 100

// ===== ATR 動態止盈止損參數 =====
atrLen = input.int(14, "ATR 週期")
atrMultiplierSL = input.float(1.1, "停損 ATR 倍數 (SL)")
atrMultiplierTP = input.float(1.6, "止盈 ATR 倍數 (TP)")

higherTF = mode == "模式 A：15m + 5m" ? "15" : "60"
lowerTF  = mode == "模式 A：15m + 5m" ? "5" : "15"
correctTF = timeframe.period == lowerTF

// ===== 1. 本週期（小子級）指標計算 =====
fastMA = ta.ema(close, 7)   // 黃線 (EMA 7)
slowMA = ta.ema(close, 22)  // 藍線 (EMA 22)

ema85  = ta.ema(close, emaLongLen1)
ema120 = ta.ema(close, emaLongLen2)

[m, s, h] = ta.macd(close, 10, 20, 7)
rsiVal = ta.rsi(close, 6)
myAtr = ta.atr(atrLen)

// ===== 2. 高週期 (大級別) 背景跨週期抓取 =====
[htf_m, htf_s, htf_h] = request.security(syminfo.tickerid, higherTF, ta.macd(close, 10, 20, 7), barmerge.gaps_off, barmerge.lookahead_off)
htf_bearish = htf_m[1] < htf_s[1]

// ===== 3. 條件判定 =====
crossoverBear = ta.crossunder(fastMA, slowMA)
priceBelow    = close < slowMA               
macdBear      = m < s                        

// RSI 過熱條件：只要過去 6 根 K 線內 RSI 曾達到過超買門檻 (>=60) 即可
wasOverbought = ta.highest(rsiVal, 6) >= rsiOverbought
rsiValid      = wasOverbought

roomToEma85   = (close - ema85) / ema85
roomToEma120  = (close - ema120) / ema120
hasEnoughRoom = (roomToEma85 >= minRoomPct) and (roomToEma120 >= minRoomPct)

emaSpread     = math.abs(fastMA - slowMA) / close
notTooFlat    = emaSpread >= 0.0003

baseCondition = correctTF and htf_bearish and macdBear and rsiValid and hasEnoughRoom and notTooFlat
nearCross     = baseCondition and (fastMA > slowMA) and ((fastMA - slowMA) / close < 0.0012) and (close < fastMA)
shortSignal   = barstate.isconfirmed and baseCondition and crossoverBear and priceBelow

// ===== 4. 動態止盈止損計算與鎖定 =====
var float entryPrice = na
var float atrSL = na
var float atrTP = na

if shortSignal
    entryPrice := close
    atrSL := close + (myAtr * atrMultiplierSL) 
    atrTP := close - (myAtr * atrMultiplierTP) 

bool hitSL = not na(entryPrice) and close >= atrSL
bool hitTP = not na(entryPrice) and close <= atrTP

if hitSL or hitTP
    entryPrice := na
    atrSL := na
    atrTP := na

// ===== 5. 圖表繪製 =====
plot(fastMA, "黃線 (EMA 7)", color=color.orange, linewidth=1)
plot(slowMA, "藍線 (EMA 22)", color=color.blue, linewidth=1)
plot(ema85,  "大級別 EMA 85", color=color.purple, linewidth=2)
plot(ema120, "大級別 EMA 120", color=color.fuchsia, linewidth=2)

plot(atrSL, "ATR 停損價 (SL)", color=color.red, style=plot.style_linebr, linewidth=2)
plot(atrTP, "ATR 止盈價 (TP)", color=color.green, style=plot.style_linebr, linewidth=2)

plotshape(shortSignal, title="SHORT", style=shape.labeldown, location=location.abovebar, color=color.red, text="SHORT", textcolor=color.white, size=size.small)

// ===== 6. 觸發即時警報 (Alert) =====
if nearCross
    alert("⚠️ 【即將死叉預警】目標幣種準備下穿，請盯緊盤面！", alert.freq_once_per_bar)

if shortSignal
    alert("🚨 【SHORT 確認】大趨勢背景 + RSI超買過熱確認！", alert.freq_once_per_bar)

// ===== 7. 狀態檢核面板 =====
var table panel = table.new(position.top_right, 1, 3)
if barstate.islast
    table.cell(panel, 0, 0, "智能預警策略 (RSI過熱確認版) ｜ " + mode, text_color=color.white, bgcolor=color.black)
    string statusText = baseCondition ? "背景條件就緒(等待交叉)✓" : "條件未備齊(等待中)..."
    table.cell(panel, 0, 1, statusText, text_color=color.white, bgcolor=baseCondition ? color.navy : color.maroon)
    table.cell(panel, 0, 2, nearCross ? "⚠️ 即將死叉發生中！" : "監控中...", text_color=color.white, bgcolor=color.gray)