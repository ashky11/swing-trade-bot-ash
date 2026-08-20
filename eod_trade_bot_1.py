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

MODEL_PRIORITY = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"]
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

def fetch_chartink_breakouts():
    """Dynamically fetches real-time EOD breakout candidates from Chartink."""
    url = "https://chartink.com/screener/process"

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Referer': 'https://chartink.com/screener/',
        'X-Requested-With': 'XMLHttpRequest',
    })
    
    try:
        r = session.get("https://chartink.com/screener/", timeout=30)
        soup = BeautifulSoup(r.text, "html.parser")
        csrf_meta = soup.select_one("[name='csrf-token']")
        if not csrf_meta:
            print("CSRF token not found on Chartink page.")
            return []
        csrf_token = csrf_meta['content']
        session.headers.update({'x-csrf-token': csrf_token})
        
        post_data = {
            "scan_clause": "( latest close > 1 day ago max(20, latest high ) and latest volume > latest sma(latest volume,20) * 1.5 and latest rsi(14) > 55 )",
            "draw": "1",
            "start": "0",
            "length": "200",
        }
        response = session.post(url, data=post_data, timeout=30)
        print(f"Chartink status: {response.status_code}")
        print(f"Chartink raw response: {response.text[:500]}")
        data = response.json()
        df = pd.DataFrame(data.get('data', []))
        print(f"Chartink rows returned: {len(df)}")

        if df.empty:
            return []
            
        # Select the top 5 highest volume breakout stocks of the day
        df = df.sort_values(by='volume', ascending=False).head(5)
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

    atr = stock_data.get('atr_14')
    if not atr or atr <= 0:
        atr = round(stock_data.get('close', 0) * 0.04, 2)
        stock_data['atr_14'] = atr

    prompt = f"""
You are a highly conservative, automated institutional swing trading desk analyst for the NSE.
Your task is to parse raw EOD financial records, verify mathematical compliance against a strict
risk management framework, and issue a flawless execution plan.

RAW INPUT DATA:
{json.dumps(stock_data, indent=2)}

ACCOUNT PARAMETERS:
- Account Capital: ₹{TOTAL_CAPITAL}
- Max Risk Per Trade: ₹{MAX_RISK_PER_TRADE} (Hard limit, never exceed)

CRITICAL EXECUTION ALGORITHM — APPLY STEPS CHRONOLOGICALLY:
1. Distance to 52-Week High:
   - Compute EXACTLY: distance_pct = ((high_52w - close) / high_52w) * 100
   - MANDATORY: If distance_pct <= 2.0%, issue SKIP immediately. No exceptions.

2. Volume Verification:
   - MANDATORY: If volume_ratio < 1.5, issue SKIP immediately.

3. Stop Loss:
   - atr_sl_level = close - (1.5 * atr_14)
   - chosen_sl = whichever is CLOSER to close: atr_sl_level OR support_20d
   - per_share_risk = close - chosen_sl

4. Target Price:
   - target = close + (2 * per_share_risk) — strict 1:2 minimum

5. Quantity:
   - quantity = floor(₹{MAX_RISK_PER_TRADE} / per_share_risk), minimum 1

OUTPUT: A single JSON object. Fill the mathematical_scratchpad first to anchor your calculations.
{{
  "mathematical_scratchpad": {{
    "calculated_distance_to_52w_high_percent": <float>,
    "calculated_atr_sl_level": <float>,
    "chosen_sl": <float>,
    "per_share_risk": <float>,
    "required_target_price": <float>
  }},
  "stock_symbol": "{stock_data.get('nsecode')}",
  "verdict": "<APPLY or SKIP>",
  "entry_price": {stock_data.get('close')},
  "stop_loss": <copy chosen_sl>,
  "target_price": <copy required_target_price>,
  "quantity": <integer>,
  "trade_reasoning": "<clinical summary citing exact metrics; if skipped, state exact rule breached>"
}}
"""

    for active_m in models_to_try:
        for attempt in range(1, 4):
            try:
                print(f"[{active_m}] Analyzing {stock_data['nsecode']} (attempt {attempt})...", end=" ", flush=True)

                def _call():
                    text = ""
                    for chunk in gemini_client.models.generate_content_stream(
                        model=active_m,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=0.0,
                        )
                    ):
                        if chunk.text:
                            text += chunk.text
                            print(".", end="", flush=True)
                    return text

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(_call)
                    try:
                        full_text = future.result(timeout=120)
                    except concurrent.futures.TimeoutError:
                        print(f"\n[{active_m} - Attempt {attempt}] Stream timed out after 120s for {stock_data['nsecode']}")
                        time.sleep(3 * attempt)
                        continue
                print()
                result = json.loads(full_text.strip())
                # Hard-enforce 1:2 using scratchpad values if available, else use result fields
                scratch = result.get('mathematical_scratchpad', {})
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

            # Hard pre-filter: reject stocks within 2% of 52W high before Gemini sees them
            high_52w = stock.get('high_52w')
            if high_52w and high_52w > 0:
                pct_below = (high_52w - stock['close']) / high_52w * 100
                if pct_below < 2.0:
                    print(f"Pre-filtered {stock['nsecode']} — {pct_below:.2f}% below 52W high (hard 2% rule)")
                    continue

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
                reason = plan.get('trade_reasoning', 'No reason provided')[:120] if plan else 'Gemini returned no result'
                print(f"Skipped {stock['nsecode']} — {reason}")
