import os
import json
import requests
import traceback
import pandas as pd
from bs4 import BeautifulSoup
from google import genai

# Read Environment Secrets safely from GitHub Actions
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TOTAL_CAPITAL = 10000       # ₹10,000 Base Capital
MAX_RISK_PER_TRADE = 200    # ₹200 Max Risk (2%)

# Backtest dataset from the Friday session
FRIDAY_BREAKOUT_CANDIDATES = [
    {"nsecode": "TRENT", "close": 7120.50, "per_chg": 4.85, "volume": 2450000},
    {"nsecode": "BEL", "close": 308.40, "per_chg": 3.40, "volume": 18500000},
    {"nsecode": "ZOMATO", "close": 265.20, "per_chg": 5.10, "volume": 42000000}
]

def analyze_with_gemini(stock_data):
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = f"""
You are a professional swing trading desk analyst. Analyze this EOD stock data:
Stock Data: {json.dumps(stock_data)}

ACCOUNT PARAMETERS:
- Capital Base: ₹{TOTAL_CAPITAL}
- Maximum Risk Allowed per Trade: ₹{MAX_RISK_PER_TRADE} (2%)

TASK:
Evaluate if this stock setup is worth taking. Provide trade parameters.
Strictly return ONLY valid JSON matching this schema with no markdown surrounding it:
{{
  "stock_symbol": "{stock_data['nsecode']}",
  "verdict": "APPLY",
  "entry_price": {stock_data['close']},
  "stop_loss": {round(stock_data['close'] * 0.96, 2)},
  "target_price": {round(stock_data['close'] * 1.08, 2)},
  "quantity": {max(1, int(MAX_RISK_PER_TRADE / (stock_data['close'] * 0.04)))},
  "trade_reasoning": "Strong momentum breakout with high volume expansion."
}}
"""
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt
        )
        
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.endswith("```"):
            text = text[:-3]
        return json.loads(text.strip())
    except Exception as e:
        print(f"Gemini Analysis Error for {stock_data.get('nsecode')}: {e}")
        traceback.print_exc()
        return None

def send_telegram_alert(message):
    try:
        telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        res = requests.post(telegram_url, data=payload, timeout=10)
        print(f"Telegram response: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"Failed to send Telegram alert: {e}")

if __name__ == "__main__":
    print("Testing backtest data for Friday session...")
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
