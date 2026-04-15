import os
from dotenv import load_dotenv

load_dotenv()

# APIs
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Apuestas
SPORTS = ["soccer_spain_la_liga", "soccer_england_premier_league"]
BOOKMAKERS = ["pinnacle", "betfair", "bet365", "unibet"]
MIN_EV_THRESHOLD = 0.03
KELLY_FRACTION = 0.25
MAX_STAKE_EUR = 50

# URLs
BASE_URL_ODDS = "https://api.the-odds-api.com/v4"