import os
import json
import requests
import pandas as pd
from bs4 import BeautifulSoup
from google import genai

# Read Environment Secrets safely from GitHub Actions
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TOTAL_CAPITAL = 10000       # ₹10,000 Base Capital
MAX_RISK_PER_TRADE = 200    # ₹200 Max Risk (2%)

def fetch_chartink_breakouts():
    url = "https://chartink.com/screener/process"
    scan_clause = {
        "scan_clause": "( {33619} ( latest close > latest max(20, latest high ) and latest volume > latest sma(volume,20) * 1.5 and latest rsi(14) > 55 ) )"
    }
    
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    
    try:
        r = session.get("https://chartink.com/screener/copy-short-term-breakouts-33")
        soup = BeautifulSoup(r.text, "html.parser")
        csrf_token = soup.select_one("[name='csrf-token']")['content']
        session.headers.update({'x-csrf-token': csrf_token})
        
        response = session.post(url, data=scan_clause)
        data = response.json()
        df = pd.DataFrame(data['data'])
        
        if df.empty:
            return []
            
        df = df.sort_values(by='volume', ascending=False).head(3)
        return df[['nsecode', 'close', 'per_chg', 'volume']].to_dict('records')
    except Exception as e:
        print(f"Error fetching screener data: {e}")
        return []

def analyze_with_gemini(stock_data):
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
    You are a professional swing trading desk analyst. Analyze this EOD stock data:
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
    print("Fetching EOD Breakout Candidates...")
    candidates = fetch_chartink_breakouts()
    
    if not candidates:
        send_telegram_alert("🤖 *AI Swing Trader Bot*: No breakout candidates triggered today.")
    else:
        for stock in candidates:
            print(f"Analyzing {stock['nsecode']} with Gemini...")
            plan = analyze_with_gemini(stock)
            
            if plan and plan.get("verdict") == "APPLY":
                alert_msg = f"""
🚀 *AI SWING TRADE SIGNAL*

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
