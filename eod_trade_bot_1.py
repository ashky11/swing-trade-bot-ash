import os
import json
import time
import requests
import pandas as pd
import yfinance as yf
import concurrent.futures
from datetime import date
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

# Read Environment Secrets safely from GitHub Actions
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

TOTAL_CAPITAL = 10000       # ₹10,000 Base Capital
MAX_RISK_PER_TRADE = 200    # ₹200 Max Risk (2%)

MODEL_PRIORITY = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-flash"]
gemini_client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options={"timeout": 120},  # 120s covers slow streamed responses
)

def fetch_chartink_breakouts():
    """Dynamically fetches real-time EOD breakout candidates from Chartink."""
    url = "https://chartink.com/screener/process"
    scan_clause = {
        "scan_clause": "( {33619} ( latest close > latest max(20, latest high ) and latest volume > latest sma(volume,20) * 1.5 and latest rsi(14) > 55 ) )"
    }
    
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    
    try:
        r = session.get("https://chartink.com/screener/copy-short-term-breakouts-33", timeout=30)
        soup = BeautifulSoup(r.text, "html.parser")
        csrf_meta = soup.select_one("[name='csrf-token']")
        if not csrf_meta:
            print("CSRF token not found on Chartink page.")
            return []
        csrf_token = csrf_meta['content']
        session.headers.update({'x-csrf-token': csrf_token})
        
        response = session.post(url, data=scan_clause, timeout=30)
        data = response.json()
        df = pd.DataFrame(data['data'])
        
        if df.empty:
            return []
            
        # Select the top 3 highest volume breakout stocks of the day
        df = df.sort_values(by='volume', ascending=False).head(3)
        candidates = df[['nsecode', 'close', 'per_chg', 'volume']].to_dict('records')
        enriched = []
        for stock in candidates:
            details = fetch_stock_details(stock['nsecode'])
            enriched.append({**stock, **details})
        return enriched
    except Exception as e:
        print(f"Error fetching screener data: {e}")
        return []

def fetch_stock_details(nsecode):
    """Fetches 1 year of OHLC from Yahoo Finance to compute ATR and key levels."""
    def _fetch():
        ticker = yf.Ticker(f"{nsecode}.NS")
        hist = ticker.history(period="1y")
        if hist.empty or len(hist) < 14:
            return {}
        hist['TR'] = pd.concat([
            hist['High'] - hist['Low'],
            (hist['High'] - hist['Close'].shift(1)).abs(),
            (hist['Low'] - hist['Close'].shift(1)).abs()
        ], axis=1).max(axis=1)
        atr_14 = round(hist['TR'].tail(14).mean(), 2)
        avg_vol = hist['Volume'].tail(20).mean()
        return {
            'atr_14': atr_14,
            'support_20d': round(hist['Low'].tail(20).min(), 2),
            'high_20d': round(hist['High'].tail(20).max(), 2),
            'high_52w': round(hist['High'].max(), 2),
            'low_52w': round(hist['Low'].min(), 2),
            'avg_volume_20d': int(avg_vol),
            'volume_ratio': round(hist['Volume'].iloc[-1] / avg_vol, 2) if avg_vol > 0 else 0,
        }
    try:
        for attempt in range(1, 4):
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    return executor.submit(_fetch).result(timeout=30)
            except concurrent.futures.TimeoutError:
                print(f"Timeout fetching details for {nsecode} (attempt {attempt}/3), retrying...")
                time.sleep(2 * attempt)
        print(f"All retries exhausted for {nsecode}, proceeding without enrichment.")
        return {}
    except Exception as e:
        print(f"Could not fetch details for {nsecode}: {e}")
        return {}

def get_active_model_endpoint():
    """Discovers the best available Gemini model via the SDK."""
    try:
        available = [
            m.name.replace("models/", "")
            for m in gemini_client.models.list()
            if "generateContent" in (getattr(m, "supported_generation_methods", None) or [])
        ]
        for target in MODEL_PRIORITY:
            if target in available:
                return target
        if available:
            return available[0]
    except Exception as e:
        print(f"Could not list models: {e}")
    return "gemini-3.5-flash"

def analyze_with_gemini(model_name, stock_data):
    models_to_try = [model_name] + [m for m in MODEL_PRIORITY if m != model_name]

    atr = stock_data.get('atr_14') or round(stock_data['close'] * 0.04, 2)
    prompt = f"""
You are a professional swing trading desk analyst. Analyze this EOD breakout candidate:
Stock Data: {json.dumps(stock_data)}

ACCOUNT PARAMETERS:
- Capital Base: ₹{TOTAL_CAPITAL}
- Maximum Risk Per Trade: ₹{MAX_RISK_PER_TRADE} (hard limit, never exceed)
- 14-day ATR: ₹{atr} (use this as your volatility baseline)

TASK — evaluate honestly, do NOT approve every stock:
1. SKIP if the stock is already extended (close within 2% of 52-week high with no room left).
2. SKIP if volume_ratio < 1.5 (no real volume confirmation).
3. SKIP if the breakout risk/reward cannot reach 1:2 within a realistic 5-10 day holding period.
4. APPLY only when you are genuinely confident in the setup.

PRICING RULES (mandatory):
- stop_loss: place at the nearest natural support level (20-day low, or 1.5× ATR below close — use whichever is closer to entry)
- target_price: MUST equal entry_price + 2 × (entry_price − stop_loss) — strict 1:2 minimum
- quantity: floor(₹{MAX_RISK_PER_TRADE} ÷ (entry_price − stop_loss)), minimum 1
- trade_reasoning: cite specific numbers from the data (ATR, volume_ratio, distance to 52w high, etc.)

Respond with a JSON object matching this schema:
{{
  "stock_symbol": "<symbol>",
  "verdict": "<APPLY or SKIP>",
  "entry_price": <number>,
  "stop_loss": <number>,
  "target_price": <number>,
  "quantity": <integer>,
  "trade_reasoning": "<reasoning citing actual numbers>"
}}
"""

    for active_m in models_to_try:
        for attempt in range(1, 4):
            try:
                print(f"[{active_m}] Analyzing {stock_data['nsecode']} (attempt {attempt})...", end=" ", flush=True)
                full_text = ""
                for chunk in gemini_client.models.generate_content_stream(
                    model=active_m,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.3,
                    )
                ):
                    if chunk.text:
                        full_text += chunk.text
                        print(".", end="", flush=True)
                print()
                result = json.loads(full_text.strip())
                # Hard-enforce 1:2 regardless of what Gemini returned
                risk = round(result['entry_price'] - result['stop_loss'], 2)
                if risk > 0:
                    min_target = round(result['entry_price'] + 2 * risk, 2)
                    if result.get('target_price', 0) < min_target:
                        result['target_price'] = min_target
                    result['quantity'] = max(1, int(MAX_RISK_PER_TRADE / risk))
                return result
            except Exception as e:
                err = str(e)
                print(f"\n[{active_m} - Attempt {attempt}] Error for {stock_data['nsecode']}: {err}")
                if any(w in err.lower() for w in ["resource_exhausted", "unavailable", "quota", "429", "timeout", "timed out"]):
                    time.sleep(3 * attempt)
                    continue
                break

    return None

def send_telegram_alert(message):
    try:
        telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        res = requests.post(telegram_url, data=payload, timeout=30)
        print(f"Telegram response: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"Failed to send Telegram alert: {e}")

def _escape_md(text):
    """Escapes Telegram Markdown v1 special chars in dynamic content."""
    return str(text).replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')

if __name__ == "__main__":
    print("Fetching live breakout candidates from Chartink...")
    candidates = fetch_chartink_breakouts()
    
    if not candidates:
        print("No breakout candidates found for today.")
        send_telegram_alert("🤖 *AI Swing Trader Bot*: No breakout candidates triggered today.")
    else:
        active_model = get_active_model_endpoint()
        print(f"Using Model: {active_model}")
        
        for stock in candidates:
            print(f"Analyzing {stock['nsecode']}...")
            plan = analyze_with_gemini(active_model, stock)
            
            if plan and plan.get("verdict") == "APPLY":
                risk = round(plan['entry_price'] - plan['stop_loss'], 2)
                reward = round(plan['target_price'] - plan['entry_price'], 2)
                rr = round(reward / risk, 1) if risk > 0 else 0
                alert_msg = f"""🚀 *AI SWING TRADE SIGNAL*
📅 *Date:* {date.today().strftime('%d %b %Y')}

📌 *Stock:* `{plan['stock_symbol']}`
🎯 *Action:* Place GTT Buy Order
───────────────
• *Entry Price:* ₹{plan['entry_price']}
• *Stop Loss:* ₹{plan['stop_loss']} \_(risk ₹{risk}/share)\_
• *Target (1:{rr}):* ₹{plan['target_price']}
• *Quantity:* {plan['quantity']} shares
• *Max Capital at Risk:* ₹{round(risk * plan['quantity'], 0)}
───────────────
💡 *Rationale:* {_escape_md(plan['trade_reasoning'])}"""
                send_telegram_alert(alert_msg)
                time.sleep(1)
            else:
                print(f"Skipped {stock['nsecode']}")
