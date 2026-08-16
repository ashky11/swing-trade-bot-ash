import os
import json
import requests
import traceback

# Read Environment Secrets safely from GitHub Actions
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TOTAL_CAPITAL = 10000       # ₹10,000 Base Capital
MAX_RISK_PER_TRADE = 200    # ₹200 Max Risk (2%)

# Backtest dataset from Friday session
FRIDAY_BREAKOUT_CANDIDATES = [
    {"nsecode": "TRENT", "close": 7120.50, "per_chg": 4.85, "volume": 2450000},
    {"nsecode": "BEL", "close": 308.40, "per_chg": 3.40, "volume": 18500000},
    {"nsecode": "ZOMATO", "close": 265.20, "per_chg": 5.10, "volume": 42000000}
]

def analyze_with_gemini(stock_data):
    # Direct REST API endpoint
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    
    prompt = f"""
You are a professional swing trading desk analyst. Analyze this EOD stock data:
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
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        data = response.json()
        
        if "error" in data:
            print(f"API Error for {stock_data['nsecode']}: {data['error']}")
            return None
            
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
            
        return json.loads(raw_text.strip())
    except Exception as e:
        print(f"Parsing error for {stock_data['nsecode']}: {e}")
        return None

def send_telegram_alert(message):
    try:
        telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": str(TELEGRAM_CHAT_ID).strip(),
            "text": message,
            "parse_mode": "Markdown"
        }
        res = requests.post(telegram_url, data=payload, timeout=10)
        print(f"Telegram response: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"Failed to send Telegram alert: {e}")

if __name__ == "__main__":
    print("Testing Friday 14-Aug Backtest candidates...")
    candidates = FRIDAY_BREAKOUT_CANDIDATES
    
    for stock in candidates:
        print(f"Analyzing {stock['nsecode']}...")
        plan = analyze_with_gemini(stock)
        
        if plan and plan.get("verdict") == "APPLY":
            alert_msg = f"""🚀 *AI SWING TRADE SIGNAL (14-AUG)*

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
        else:
            print(f"Skipped {stock['nsecode']}")
