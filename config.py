import os
from dotenv import load_dotenv

load_dotenv()

# APIs
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Deportes activos
SPORTS_FOOTBALL = ["soccer_spain_la_liga", "soccer_epl"]
SPORTS_TENNIS = ["tennis_atp_barcelona_open", "tennis_atp_munich", "tennis_wta_stuttgart_open"]
SPORTS = SPORTS_FOOTBALL + SPORTS_TENNIS

# Superficie por torneo (para el modelo de tenis)
TOURNAMENT_SURFACE = {
    "tennis_atp_barcelona_open": "Clay",
    "tennis_atp_munich":         "Clay",
    "tennis_wta_stuttgart_open": "Clay",
}

# Circuito por torneo
TOURNAMENT_TOUR = {
    "tennis_atp_barcelona_open": "ATP",
    "tennis_atp_munich":         "ATP",
    "tennis_wta_stuttgart_open": "WTA",
}

# Bankroll inicial
BANKROLL = 1000

# Casas
BOOKMAKERS = ["pinnacle", "betfair", "bet365", "unibet"]

# Valor
MIN_EV_THRESHOLD = 0.03
KELLY_FRACTION = 0.25
MAX_STAKE_EUR = 50

# Peso del modelo de ranking vs modelo de servicio (0=solo servicio, 1=solo ranking)
RANK_WEIGHT = 0.5

# URLs
BASE_URL_ODDS = "https://api.the-odds-api.com/v4"