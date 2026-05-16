import yfinance as yf
import anthropic
import os
from datetime import date

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

def get_stock_data(ticker):
    stock = yf.Ticker(ticker)
    hist = stock.history(period="5d")
    info = stock.fast_info
    
    # Get recent news headlines
    news = stock.news[:3] if stock.news else []
    headlines = [n.get("content", {}).get("title", "") for n in news]
    
    return {
        "ticker": ticker,
        "price": round(info.last_price, 2),
        "prev_close": round(info.previous_close, 2),
        "pct_change": round((info.last_price - info.previous_close) / info.previous_close * 100, 2),
        "volume": info.last_volume,
        "52w_high": round(info.year_high, 2),
        "52w_low": round(info.year_low, 2),
        "5d_closes": hist["Close"].round(2).tolist(),
        "recent_headlines": headlines
    }

def analyze(data):
    prompt = f"""
    You are a stock analyst writing a daily briefing for a retail investor.
    Analyze this data and be concise, direct and actionable.
    
    Stock Data: {data}
    
    Return your analysis in exactly this format:
    
    **Price Action:** (what the price did today and context)
    **Volume Signal:** (was volume high, low or normal vs average)
    **Trend & Momentum:** (short term direction, is it strengthening or weakening)
    **Market Sentiment:** (based on headlines, is mood Bullish / Bearish / Neutral)
    **Key Risk or Opportunity:** (one thing to watch tomorrow)
    **Verdict:** Watch | Hold | Act | Avoid
    """
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text

def main():
    with open("tickers.txt") as f:
        tickers = [line.strip() for line in f if line.strip()]

    today = date.today().isoformat()
    lines = [f"# 📈 Daily Stock Analysis — {today}\n"]
    lines.append(f"**Tickers Analyzed:** {', '.join(tickers)}\n")
    lines.append("---\n")

    for ticker in tickers:
        print(f"Analyzing {ticker}...")
        try:
            data = get_stock_data(ticker)
            analysis = analyze(data)
            lines.append(f"## {ticker} — ${data['price']} ({data['pct_change']:+.2f}%)\n")
            lines.append(analysis + "\n")
            lines.append("---\n")
        except Exception as e:
            lines.append(f"## {ticker}\n⚠️ Could not analyze: {e}\n---\n")

    os.makedirs("reports", exist_ok=True)
    report_path = f"reports/{today}.md"
    
    with open(report_path, "w") as f:
        f.write("\n".join(lines))

    print(f"✅ Report saved to {report_path}")

if __name__ == "__main__":
    main()
