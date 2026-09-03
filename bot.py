import datetime
import time
import pandas as pd
import requests
import yfinance as yf

# ==========================================
# CONFIGURATION
# ==========================================
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1544942560760299520/v2_0MjTNFwVIUD-GsF_ewV641MFSZRiqVpmUgHqvgnAb5iBlOYNx4F3LqT3YgeZme-Aq"  # Paste your Discord Webhook URL here
SYMBOL = "GC=F"                                        # Gold Futures on Yahoo Finance (Use "XAUUSD=X" for Spot Gold)
TIMEFRAME = "5m"                                       # 5-minute timeframe
CHECK_INTERVAL_SEC = 15                                # Check frequency in seconds for live candles

# Strategy Parameters (Adjusted for Gold)
SL_BUFFER_PRICE = 0.50                                 # $0.50 Stop Loss buffer beyond Candle 1
MAX_WICK_TO_BODY = 0.60                                # Max wick-to-body ratio for momentum candles
DOJI_THRESHOLD = 0.50                                   # Max body-to-range ratio for Doji
RR_TARGET = 2.0                                        # Target Risk:Reward Ratio (1:2)
BE_RR = 1.0                                            # Breakeven Trigger (1:1 RR)


# Helper function to compute price movement size
def get_point_size(symbol):
    return 1.0 if ("GC=F" in symbol or "XAU" in symbol) else 0.0001


# Check momentum candle criteria
def is_momentum(open_p, high_p, low_p, close_p):
    c_body = abs(close_p - open_p)
    if c_body == 0:
        return False
    top_wick = high_p - max(open_p, close_p)
    bottom_wick = min(open_p, close_p) - low_p
    return (top_wick / c_body) <= MAX_WICK_TO_BODY and (bottom_wick / c_body) <= MAX_WICK_TO_BODY


# Send embed messages to Discord Webhook
def send_discord_alert(title, message, color=0x00FF00):
    if DISCORD_WEBHOOK_URL == "YOUR_DISCORD_WEBHOOK_URL_HERE" or not DISCORD_WEBHOOK_URL:
        return
    payload = {
        "embeds": [
            {
                "title": title,
                "description": message,
                "color": color,
                "timestamp": datetime.datetime.utcnow().isoformat()
            }
        ]
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload)
    except Exception as e:
        print(f"Error sending Discord message: {e}")


# ==========================================
# HISTORICAL BACKTEST & DASHBOARD TABLE
# ==========================================
def run_strategy_backtest(symbol, timeframe):
    print(f"Running historical backtest for {symbol} ({timeframe})...")
    
    # Download up to 30 days of 5m historical data
    data = yf.download(tickers=symbol, period="30d", interval=timeframe, progress=False)
    if len(data) < 100:
        print("Insufficient historical data to run backtest.")
        return

    # Clean multi-index columns if present
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    point_size = get_point_size(symbol)
    sl_buffer = SL_BUFFER_PRICE

    total_setups = 0
    total_trades = 0
    wins = 0
    breakevens = 0
    losses = 0
    total_r = 0.0
    total_points = 0.0

    in_trade = False
    trade_dir = 0
    entry_px = 0.0
    sl_px = 0.0
    tp_px = 0.0
    be_px = 0.0
    initial_risk = 0.0
    is_be = False

    pending_buy = False
    pending_sell = False
    doji_high = 0.0
    doji_low = 0.0
    pattern_sl = 0.0

    # Process historical bars
    for i in range(2, len(data)):
        c1 = data.iloc[i - 2]
        c2 = data.iloc[i - 1]
        c3 = data.iloc[i]

        high_curr, low_curr = c3['High'], c3['Low']

        # 1. Manage active trades
        if in_trade:
            if trade_dir == -1:  # Short Position
                if not is_be and low_curr <= be_px:
                    sl_px = entry_px
                    is_be = True

                if low_curr <= tp_px:
                    pts = entry_px - tp_px
                    wins += 1
                    total_r += RR_TARGET
                    total_points += pts
                    in_trade = False
                elif high_curr >= sl_px:
                    if is_be:
                        breakevens += 1
                    else:
                        pts = sl_px - entry_px
                        losses += 1
                        total_r -= 1.0
                        total_points -= pts
                    in_trade = False

            elif trade_dir == 1:  # Long Position
                if not is_be and high_curr >= be_px:
                    sl_px = entry_px
                    is_be = True

                if high_curr >= tp_px:
                    pts = tp_px - entry_px
                    wins += 1
                    total_r += RR_TARGET
                    total_points += pts
                    in_trade = False
                elif low_curr <= sl_px:
                    if is_be:
                        breakevens += 1
                    else:
                        pts = entry_px - sl_px
                        losses += 1
                        total_r -= 1.0
                        total_points -= pts
                    in_trade = False

        # 2. Execute pending retest entries
        if pending_sell and not in_trade:
            if high_curr >= doji_low:
                in_trade = True
                pending_sell = False
                trade_dir = -1
                entry_px = doji_low
                sl_px = pattern_sl
                initial_risk = sl_px - entry_px
                tp_px = entry_px - (initial_risk * RR_TARGET)
                be_px = entry_px - (initial_risk * BE_RR)
                is_be = False
                total_trades += 1

        if pending_buy and not in_trade:
            if low_curr <= doji_high:
                in_trade = True
                pending_buy = False
                trade_dir = 1
                entry_px = doji_high
                sl_px = pattern_sl
                initial_risk = entry_px - sl_px
                tp_px = entry_px + (initial_risk * RR_TARGET)
                be_px = entry_px + (initial_risk * BE_RR)
                is_be = False
                total_trades += 1

        # Cancel invalid setups
        if pending_sell and high_curr > pattern_sl:
            pending_sell = False
        if pending_buy and low_curr < pattern_sl:
            pending_buy = False

        # 3. Detect new setups
        c2_range = c2['High'] - c2['Low']
        c2_body = abs(c2['Close'] - c2['Open'])
        is_doji2 = c2_range > 0 and (c2_body / c2_range) <= DOJI_THRESHOLD

        # Bearish Setup
        is_bearish1 = (c1['Close'] < c1['Open']) and is_momentum(c1['Open'], c1['High'], c1['Low'], c1['Close'])
        is_bearish2 = (c2['Close'] <= c2['Open']) and is_doji2
        is_bearish3 = (c3['Close'] < c3['Open']) and is_momentum(c3['Open'], c3['High'], c3['Low'], c3['Close']) and (c3['Close'] < max(c2['Close'], c2['Low']))

        if is_bearish1 and is_bearish2 and is_bearish3 and not in_trade:
            pending_sell = True
            pending_buy = False
            doji_high = c2['High']
            doji_low = c2['Low']
            pattern_sl = c1['High'] + sl_buffer
            total_setups += 1

        # Bullish Setup
        is_bullish1 = (c1['Close'] > c1['Open']) and is_momentum(c1['Open'], c1['High'], c1['Low'], c1['Close'])
        is_bullish2 = (c2['Close'] >= c2['Open']) and is_doji2
        is_bullish3 = (c3['Close'] > c3['Open']) and is_momentum(c3['Open'], c3['High'], c3['Low'], c3['Close']) and (c3['Close'] > min(c2['Close'], c2['High']))

        if is_bullish1 and is_bullish2 and is_bullish3 and not in_trade:
            pending_buy = True
            pending_sell = False
            doji_high = c2['High']
            doji_low = c2['Low']
            pattern_sl = c1['Low'] - sl_buffer
            total_setups += 1

    # Calculate Win Rate (Wins + Breakevens)
    winrate = (((wins + breakevens) / total_trades) * 100) if total_trades > 0 else 0.0

    # Display Dashboard Table
    summary = f"""
