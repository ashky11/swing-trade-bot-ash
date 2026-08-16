import os
import json
import time
import requests
import traceback
import pandas as pd
from bs4 import BeautifulSoup

# Read Environment Secrets safely from GitHub Actions
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

TOTAL_CAPITAL = 10000       # ₹10,000 Base Capital
MAX_RISK_PER_TRADE = 200    # ₹200 Max Risk (2%)

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
        csrf_token = soup.select_one("[name='csrf-token']")['content']
        session.headers.update({'x-csrf-token': csrf_token})
        
        response = session.post(url, data=scan_clause, timeout=30)
        data = response.json()
        df = pd.DataFrame(data['data'])
        
        if df.empty:
            return []
            
        # Select the top 3 highest volume breakout stocks of the day
        df = df.sort_values(by='volume', ascending=False).head(3)
        return df[['nsecode', 'close', 'per_chg', 'volume']].to_dict('records')
    except Exception as e:
        print(f"Error fetching screener data: {e}")
        return []

def get_active_model_endpoint():
    """Dynamically discovers supported models for this API key."""
    list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    try:
        res = requests.get(list_url, timeout=30)
        data = res.json()
        if "models" in data:
            available_names = [
                m["name"].replace("models/", "") 
                for m in data["models"] 
                if "generateContent" in m.get("supportedGenerationMethods", [])
            ]
            for target in ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-flash-latest"]:
                if target in available_names:
                    return target
            if available_names:
                return available_names[0]
    except Exception as e:
        print(f"Could not list models dynamically: {e}")
    return "gemini-3.5-flash"

def analyze_with_gemini(model_name, stock_data):
    models_to_try = [model_name, "gemini-2.5-flash", "gemini-2.5-flash-lite"]
    seen = set()
    models_to_try = [m for m in models_to_try if not (m in seen or seen.add(m))]

    prompt = f"""
You are a professional swing trading desk analyst. Analyze this live EOD stock data:
Stock Data: {json.dumps(stock_data)}

ACCOUNT PARAMETERS:
- Capital Base: ₹{TOTAL_CAPITAL}
- Maximum Risk Allowed per Trade: ₹{MAX_RISK_PER_TRADE} (2%)

TASK:
Evaluate if this stock setup is worth taking. Provide trade parameters.
Strictly return ONLY valid JSON matching this schema with no extra text or markdown formatting:
{{
  "stock_symbol": "{stock_data['nsecode']}",
  "verdict": "APPLY",
  "entry_price": {stock_data['close']},
  "stop_loss": {round(stock_data['close'] * 0.96, 2)},
  "target_price": {round(stock_data['close'] * 1.08, 2)},
  "quantity": {max(1, int(MAX_RISK_PER_TRADE / (stock_data['close'] * 0.04)))},
  "trade_reasoning": "Strong breakout setup with expanding volume structure."
}}
"""
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }

    for active_m in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{active_m}:generateContent?key={GEMINI_API_KEY}"
        
        for attempt in range(1, 4):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=60)
                data = response.json()
                
                if "error" in data:
                    err_msg = data['error'].get('message', '')
                    print(f"[{active_m} - Attempt {attempt}] API Error for {stock_data['nsecode']}: {err_msg}")
                    if any(w in err_msg.lower() for w in ["high demand", "unavailable", "resource exhausted"]):
                        time.sleep(3 * attempt)
                        continue
                    break
                    
                raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                if raw_text.startswith("```json"):
                    raw_text = raw_text[7:]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3]
                    
                return json.loads(raw_text.strip())
            except Exception as e:
                print(f"[{active_m} - Attempt {attempt}] Error for {stock_data['nsecode']}: {e}")
                time.sleep(2)

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
                alert_msg = f"""🚀 *AI SWING TRADE SIGNAL*

📌 *Stock:* `{plan['stock_symbol']}`
🎯 *Action:* Place GTT Buy Order
───────────────
• *Entry Price:* ₹{plan['entry_price']}
• *Stop Loss:* ₹{plan['stop_loss']}
• *Target (1:2):* ₹{plan['target_price']}
• *Quantity to Buy:* {plan['quantity']} Shares
• *Total Risk:* ₹{MAX_RISK_PER_TRADE}
───────────────
💡 *Rationale:* {plan['trade_reasoning']}"""
                send_telegram_alert(alert_msg)
                time.sleep(1)
            else:
                print(f"Skipped {stock['nsecode']}")
