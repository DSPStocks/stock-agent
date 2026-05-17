import yfinance as yf
from groq import Groq
import os
import json
from datetime import date, datetime, timedelta
import time

client = Groq(api_key=os.environ["GROQ_API_KEY"])

# ─────────────────────────────────────────
# TRADING DAYS HELPER
# ─────────────────────────────────────────
US_HOLIDAYS_2026 = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 4, 3), date(2026, 5, 25), date(2026, 7, 3),
    date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)
}

def trading_days_between(start, end):
    """Count trading days between two dates."""
    count = 0
    current = start
    while current < end:
        if current.weekday() < 5 and current not in US_HOLIDAYS_2026:
            count += 1
        current += timedelta(days=1)
    return count

def trading_days_ago(from_date, n):
    """Return the date that was n trading days before from_date."""
    count = 0
    current = from_date - timedelta(days=1)
    while count < n:
        if current.weekday() < 5 and current not in US_HOLIDAYS_2026:
            count += 1
        if count < n:
            current -= timedelta(days=1)
    return current

# ─────────────────────────────────────────
# EARNINGS LOG — remembers past earnings
# ─────────────────────────────────────────
EARNINGS_LOG = "earnings_log.json"

def load_earnings_log():
    if os.path.exists(EARNINGS_LOG):
        with open(EARNINGS_LOG) as f:
            return json.load(f)
    return {}

def save_earnings_log(log):
    with open(EARNINGS_LOG, "w") as f:
        json.dump(log, f, indent=2)

def update_earnings_log(ticker, earnings_date_str, actual_eps=None,
                         actual_rev=None, beat=None):
    log = load_earnings_log()
    if ticker not in log:
        log[ticker] = {}
    log[ticker]["earnings_date"] = earnings_date_str
    if actual_eps is not None:
        log[ticker]["actual_eps"] = actual_eps
    if actual_rev is not None:
        log[ticker]["actual_revenue"] = actual_rev
    if beat is not None:
        log[ticker]["beat"] = beat
    save_earnings_log(log)

# ─────────────────────────────────────────
# DETERMINE WHICH MODE WE ARE IN
# ─────────────────────────────────────────
def get_earnings_mode(ticker, info, stock):
    """
    Returns one of:
      'pre'   — earnings within next 7 trading days
      'day0'  — earnings reported today
      'post'  — within 7 trading days AFTER earnings
      'none'  — no earnings event nearby
    Along with all relevant earnings data.
    """
    today = date.today()
    log = load_earnings_log()
    result = {
        "mode": "none",
        "earnings_date": None,
        "days_to_earnings": None,
        "trading_days_to_earnings": None,
        "trading_days_since_earnings": None,
        "eps_estimate": None,
        "revenue_estimate": None,
        "actual_eps": None,
        "actual_revenue": None,
        "beat_eps": None,
        "beat_revenue": None,
        "eps_surprise_pct": None,
        "guidance": None,
        "beat_count": 0,
        "miss_count": 0,
        "last_surprise_pct": None,
        "implied_move_pct": None,
        "logged_earnings_date": None,
    }

    # ── Check post-earnings from log ──
    if ticker in log and "earnings_date" in log[ticker]:
        logged_date = datetime.strptime(
            log[ticker]["earnings_date"], "%Y-%m-%d").date()
        td_since = trading_days_between(logged_date, today)
        if 0 < td_since <= 7:
            result["mode"] = "post"
            result["logged_earnings_date"] = str(logged_date)
            result["trading_days_since_earnings"] = td_since
            result["actual_eps"] = log[ticker].get("actual_eps")
            result["actual_revenue"] = log[ticker].get("actual_revenue")
            result["beat_eps"] = log[ticker].get("beat")

    # ── Get upcoming earnings date ──
    try:
        cal = stock.calendar
        if cal is not None and "Earnings Date" in cal:
            ed = cal["Earnings Date"]
            if hasattr(ed, '__iter__'):
                ed = list(ed)[0]
            if hasattr(ed, 'date'):
                ed = ed.date()
            elif isinstance(ed, str):
                ed = datetime.strptime(ed, "%Y-%m-%d").date()

            result["earnings_date"] = str(ed)
            calendar_days = (ed - today).days
            result["days_to_earnings"] = calendar_days

            if ed == today:
                result["mode"] = "day0"
                update_earnings_log(ticker, str(ed))
            elif 0 < calendar_days <= 10:
                td = trading_days_between(today, ed)
                result["trading_days_to_earnings"] = td
                if td <= 7 and result["mode"] != "post":
                    result["mode"] = "pre"
    except:
        pass

    # ── EPS and revenue estimates ──
    try:
        result["eps_estimate"] = info.get("forwardEps")
    except:
        pass

    # ── Beat/miss history ──
    try:
        history = stock.earnings_history
        if history is not None and not history.empty:
            recent = history.tail(8)
            beats = misses = 0
            for _, row in recent.iterrows():
                try:
                    actual = float(row.get("epsActual") or 0)
                    est = float(row.get("epsEstimate") or 0)
                    if actual and est:
                        if actual >= est:
                            beats += 1
                        else:
                            misses += 1
                except:
                    pass
            result["beat_count"] = beats
            result["miss_count"] = misses
            try:
                last = history.iloc[-1]
                surprise = last.get("surprisePercent")
                if surprise:
                    result["last_surprise_pct"] = round(
                        float(surprise) * 100, 1)
            except:
                pass
    except:
        pass

    # ── Actual reported results (day0 or post) ──
    if result["mode"] in ("day0", "post") and not result["actual_eps"]:
        try:
            hist = stock.earnings_history
            if hist is not None and not hist.empty:
                last = hist.iloc[-1]
                actual = float(last.get("epsActual") or 0)
                est = float(last.get("epsEstimate") or 0)
                if actual:
                    result["actual_eps"] = round(actual, 2)
                    result["eps_estimate"] = round(est, 2)
                    surprise = ((actual - est) / abs(est) * 100) if est else 0
                    result["eps_surprise_pct"] = round(surprise, 1)
                    result["beat_eps"] = actual >= est
                    update_earnings_log(
                        ticker,
                        result["logged_earnings_date"] or result["earnings_date"],
                        actual_eps=actual,
                        beat=actual >= est
                    )
        except:
            pass

    # ── Implied move from options ──
    try:
        opts = stock.options
        if opts:
            nearest = None
            for exp in opts:
                exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                if exp_date >= today:
                    nearest = exp
                    break
            if nearest:
                chain = stock.option_chain(nearest)
                cp = stock.fast_info.last_price
                atm_c = chain.calls.iloc[
                    (chain.calls['strike'] - cp).abs().argsort()[:1]]
                atm_p = chain.puts.iloc[
                    (chain.puts['strike'] - cp).abs().argsort()[:1]]
                straddle = (float(atm_c['lastPrice'].values[0]) +
                            float(atm_p['lastPrice'].values[0]))
                result["implied_move_pct"] = round(
                    (straddle / cp) * 100, 1)
    except:
        pass

    return result

# ─────────────────────────────────────────
# PULL ALL STOCK DATA
# ─────────────────────────────────────────
def get_stock_data(ticker):
    stock = yf.Ticker(ticker)
    hist = stock.history(period="15d")
    info = stock.info
    fast = stock.fast_info

    data = {
        "ticker": ticker,
        "price": round(fast.last_price, 2),
        "prev_close": round(fast.previous_close, 2),
        "pct_change": round((fast.last_price - fast.previous_close)
                            / fast.previous_close * 100, 2),
        "volume": fast.last_volume,
        "avg_volume": info.get("averageVolume", 0),
        "52w_high": round(fast.year_high, 2),
        "52w_low": round(fast.year_low, 2),
        "15d_closes": hist["Close"].round(2).tolist(),
        "headlines": [],
    }

    # News
    try:
        news = stock.news[:5] if stock.news else []
        data["headlines"] = [
            n.get("content", {}).get("title", "") for n in news
        ]
    except:
        pass

    # Earnings intelligence
    earnings = get_earnings_mode(ticker, info, stock)
    data.update(earnings)

    return data

# ─────────────────────────────────────────
# BUILD THE AI PROMPT BY MODE
# ─────────────────────────────────────────
def build_prompt(data):
    base = f"""
Ticker: {data['ticker']}
Price: ${data['price']} ({data['pct_change']:+.2f}% today)
Volume: {data['volume']:,} vs avg {data['avg_volume']:,}
52-week range: ${data['52w_low']} — ${data['52w_high']}
15-day closes: {data['15d_closes']}
Recent headlines: {data['headlines']}
"""

    mode = data.get("mode", "none")

    # ── PRE EARNINGS ──
    if mode == "pre":
        total = data['beat_count'] + data['miss_count']
        beat_rate = round(data['beat_count'] / total * 100) if total else 0
        earnings_block = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚡ EARNINGS IN {data['trading_days_to_earnings']} TRADING DAYS
   Date: {data['earnings_date']}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EPS Estimate:        {data['eps_estimate']}
Beat Rate:           {data['beat_count']}/{total} quarters ({beat_rate}%)
Last Surprise:       {data['last_surprise_pct']}%
Implied Move:        ±{data['implied_move_pct']}%
"""
        instructions = """
You are a senior equity analyst. Write a full pre-earnings briefing.

FORMAT EXACTLY:
**Price Action:** ...
**Volume Signal:** ...
**Trend & Momentum:** ...
**Market Sentiment:** (Bullish/Bearish/Neutral + reason from headlines)

**Earnings Preview:**
- What the market is expecting (EPS, narrative)
- Beat probability based on track record
- What options market is implying (±X% move)
- Key things to watch in the report

**If BEAT — Day by Day (7 trading days):**
Day 1: price target and % move
Day 2: expected behavior
Day 3: expected behavior
Day 4-5: expected behavior
Day 6-7: expected behavior
Overall: BUY or HOLD with target price

**If MISS — Day by Day (7 trading days):**
Day 1: price target and % move
Day 2: expected behavior
Day 3: expected behavior
Day 4-5: expected behavior
Day 6-7: expected behavior
Overall: AVOID or SELL with support level

**Verdict:** Buy | Watch | Hold | Avoid — one line reason
"""

    # ── EARNINGS DAY ──
    elif mode == "day0":
        earnings_block = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔔 EARNINGS REPORTED TODAY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EPS Actual:    {data['actual_eps']}
EPS Estimate:  {data['eps_estimate']}
EPS Surprise:  {data['eps_surprise_pct']}%
Beat EPS:      {'✅ YES' if data['beat_eps'] else '❌ NO'}
"""
        instructions = """
You are a senior equity analyst covering earnings day.

FORMAT EXACTLY:
**Price Action:** ...
**Volume Signal:** ...

**Earnings Result:**
- EPS beat or miss with exact numbers
- Revenue beat or miss
- Guidance raised, maintained, or cut
- One key thing management said

**vs Market Expectations:**
- Was this a strong beat, inline, or miss?
- How does today's price reaction compare to the implied move?
- Is the market reaction appropriate or overdone?

**What Happens Next — 7 Trading Days:**
Day 1 (today): current reaction and what to watch into close
Day 2: likely behavior based on beat/miss
Day 3: expected behavior
Day 4-5: expected behavior
Day 6-7: expected behavior and new support/resistance

**Verdict:** Buy | Hold | Avoid — with specific price levels
"""

    # ── POST EARNINGS ──
    elif mode == "post":
        earnings_block = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 DAY {data['trading_days_since_earnings']} POST-EARNINGS
   Reported: {data['logged_earnings_date']}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EPS Result:    {'✅ BEAT' if data['beat_eps'] else '❌ MISS'} 
Actual EPS:    {data['actual_eps']}
EPS Estimate:  {data['eps_estimate']}
Days Remaining in Window: {7 - data['trading_days_since_earnings']}
"""
        instructions = f"""
You are a senior equity analyst tracking post-earnings behavior.
Today is Day {data['trading_days_since_earnings']} of 7 in the post-earnings window.

FORMAT EXACTLY:
**Price Action:** ...
**Volume Signal:** ...
**Trend & Momentum:** (is the post-earnings move holding or fading?)

**Post-Earnings Scorecard:**
- What happened on earnings day
- How has the stock behaved since
- Is sentiment from headlines supporting or undermining the move
- Is institutional buying or selling visible in volume

**Remaining {7 - data['trading_days_since_earnings']} Trading Days:**
What to expect for each remaining day in the window
Key support and resistance levels to watch
Will the move hold or mean-revert?

**Updated Verdict:** Buy | Hold | Avoid — updated based on post-earnings behavior
"""

    # ── NORMAL DAY ──
    else:
        next_earnings = ""
        if data.get("earnings_date"):
            next_earnings = f"Next earnings: {data['earnings_date']} ({data['days_to_earnings']} calendar days away)"
        earnings_block = next_earnings
        instructions = """
You are a senior equity analyst writing a daily briefing.

FORMAT EXACTLY:
**Price Action:** ...
**Volume Signal:** ...
**Trend & Momentum:** ...
**Market Sentiment:** (Bullish/Bearish/Neutral + reason)
**5-Day Outlook:** what to expect this week with price levels
**Earnings Note:** when next earnings is and what to start watching
**Verdict:** Buy | Watch | Hold | Avoid — one line reason
"""

    return f"{instructions}\n\nDATA:\n{base}\n{earnings_block}"

# ─────────────────────────────────────────
# CALL THE AI
# ─────────────────────────────────────────
def analyze(data):
    prompt = build_prompt(data)
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1500
    )
    return response.choices[0].message.content

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
def main():
    with open("tickers.txt") as f:
        tickers = [line.strip() for line in f
                   if line.strip() and not line.strip().startswith("#")]

    today = date.today().isoformat()
    lines = [f"# 📈 Daily Stock Analysis — {today}\n"]
    lines.append(f"**Tickers Analyzed:** {', '.join(tickers)}\n")
    lines.append("---\n")

    for ticker in tickers:
        print(f"Analyzing {ticker}...")
        try:
            data = get_stock_data(ticker)

            # Badge based on mode
            mode = data.get("mode", "none")
            if mode == "pre":
                badge = f" ⚡ EARNINGS IN {data['trading_days_to_earnings']} TRADING DAYS"
            elif mode == "day0":
                badge = " 🔔 EARNINGS TODAY"
            elif mode == "post":
                badge = f" 📊 DAY {data['trading_days_since_earnings']} POST-EARNINGS"
            else:
                badge = ""

            analysis = analyze(data)
            lines.append(
                f"## {ticker} — ${data['price']} "
                f"({data['pct_change']:+.2f}%){badge}\n"
            )
            lines.append(analysis + "\n")
            lines.append("---\n")

        except Exception as e:
            lines.append(
                f"## {ticker}\n⚠️ Could not analyze: {e}\n---\n"
            )
        time.sleep(2)

    # Save report
    os.makedirs("reports", exist_ok=True)
    report_path = f"reports/{today}.md"
    with open(report_path, "w") as f:
        f.write("\n".join(lines))

    # Commit earnings log too
    print(f"✅ Report saved to {report_path}")

if __name__ == "__main__":
    main()
