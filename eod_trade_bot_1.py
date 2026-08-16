import os
import json
import requests
from google import genai

# Read Environment Secrets safely from GitHub Actions
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TOTAL_CAPITAL = 10000       # ₹10,000 Base Capital
MAX_RISK_PER_TRADE = 200    # ₹200 Max Risk (2%)

# Sample breakout candidates from Friday, Aug 14 EOD session
FRIDAY_BREAKOUT_CANDIDATES = [
    {
        "nsecode": "TRENT",
        "close": 7120.50,
        "per_chg": 4.85,
        "volume": 2450000
    },
    {
        "nsecode": "BEL",
        "close": 308.40,
        "per_chg": 3.40,
        "volume": 18500000
    },
    {
        "nsecode": "ZOMATO",
        "close": 265.20,
        "per_chg": 5.10,
        "volume": 42000000
    }
]

def analyze_with_gemini(stock_data):
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
    You are a professional swing trading desk analyst. Analyze this EOD stock data from the Friday close:
    Stock Data: {json.dumps(stock_data)}
    
    ACCOUNT PARAMETERS:
    - Capital Base: ₹{TOTAL_CAPITAL}
    - Maximum Risk Allowed per Trade: ₹{MAX_RISK_PER_TRADE} (2%)
    
    TASK:
    Evaluate if this stock setup is worth taking. Provide trade parameters.
    OUTPUT REQUIREMENT: Strictly return valid JSON matching this schema:
    {{
      "stock_symbol": "STRING",
      "verdict": "APPLY" or "REJECT",
      "entry_price": "NUMBER",
      "stop_loss": "NUMBER",
      "target_price": "NUMBER",
      "quantity": "INTEGER",
      "trade_reasoning": "STRING"
    }}
    """
    
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[prompt]
    )
    
    try:
        clean_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_text)
    except Exception as e:
        print(f"Failed to parse Gemini output: {e}")
        return None

def send_telegram_alert(message):
    telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    requests.post(telegram_url, data=payload)

if __name__ == "__main__":
    print("Running Backtest for Friday Session Candidates...")
    candidates = FRIDAY_BREAKOUT_CANDIDATES
    
    for stock in candidates:
        print(f"Analyzing {stock['nsecode']} with Gemini...")
        plan = analyze_with_gemini(stock)
        
        if plan and plan.get("verdict") == "APPLY":
            alert_msg = f"""
🚀 *AI SWING TRADE SIGNAL (BACKTEST: 14-AUG)*

📌 *Stock:* `{plan['stock_symbol']}`
🎯 *Action:* Place GTT Buy Order
───────────────
• *Entry Price:* ₹{plan['entry_price']}
• *Stop Loss:* ₹{plan['stop_loss']}
• *Target (1:2):* ₹{plan['target_price']}
• *Quantity to Buy:* {plan['quantity']} Shares
• *Total Risk:* ₹{MAX_RISK_PER_TRADE}
───────────────
💡 *Rationale:* {plan['trade_reasoning']}
"""
            send_telegram_alert(alert_msg)
        else:
            print(f"Stock {stock['nsecode']} rejected by AI analysis.")
