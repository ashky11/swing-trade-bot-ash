# swing-trade-bot-ash
AI-powered EOD swing trade signal bot for NSE stocks. Runs automatically every weekday at 5:00 PM IST via GitHub Actions — no server or paid infra required.

## How It Works

1. **Screener** — Fetches real-time breakout candidates from Chartink (close > 20-day high, volume ≥ 1.5× avg, RSI > 55)
2. **Enrich** — Pulls 1 year of OHLC from Yahoo Finance to compute ATR, 52-week levels, and true volume ratio per stock
3. **AI Filter** — Sends each candidate to Gemini with strict rules: skip if near 52w high, skip if volume ratio < 1.5, skip if 1:2 R/R isn't achievable
4. **Alert** — Sends approved setups to your Telegram with entry, stop loss, target, quantity, and reasoning. GTT-ready for Zerodha / Groww.

Risk is capped at ₹200 per trade (2% of ₹10,000 base capital). A 1:2 risk/reward minimum is hard-enforced in code regardless of what the AI returns.

## Setup

### 1. Fork or clone this repository

### 2. Add GitHub Secrets
Go to your repo → **Settings → Secrets and variables → Actions → New repository secret** and add all three:

| Secret Name | Where to get it |
|---|---|
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/apikey) — free tier is sufficient |
| `TELEGRAM_BOT_TOKEN` | Create a bot via [@BotFather](https://t.me/BotFather) on Telegram |
| `TELEGRAM_CHAT_ID` | Send a message to your bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id` |

### 3. Enable GitHub Actions
Go to **Actions** tab in your repo and enable workflows if prompted.

The bot will run automatically Monday–Friday at 5:00 PM IST. To trigger it manually, go to **Actions → Run EOD Swing Trade Bot → Run workflow**.

## Configuration

Edit the constants at the top of `eod_trade_bot_1.py`:

```python
TOTAL_CAPITAL = 10000      # Your base capital in ₹
MAX_RISK_PER_TRADE = 200   # Max loss per trade in ₹ (keep at 2% of capital)
```

## Sample Telegram Alert

```
🚀 AI SWING TRADE SIGNAL
📅 Date: 18 Aug 2026

📌 Stock: TATAPOWER
🎯 Action: Place GTT Buy Order
───────────────
• Entry Price: ₹432.5
• Stop Loss: ₹418.0 (risk ₹14.5/share)
• Target (1:2.1): ₹462.9
• Quantity: 13 shares
• Max Capital at Risk: ₹188.5
───────────────
💡 Rationale: Stock broke 20-day high of ₹430 on 2.3× avg volume. ATR-14 is ₹9.8. Stop placed at 20-day support ₹418. 52w high is ₹512 — 18% upside available.
```

## Dependencies

| Package | Purpose |
|---|---|
| `google-genai` | Gemini AI analysis via official SDK |
| `yfinance` | ATR, 52w high/low, support levels |
| `beautifulsoup4` | Chartink CSRF token parsing |
| `pandas` | Screener data processing |
| `requests` | Chartink + Telegram HTTP calls |
