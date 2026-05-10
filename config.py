import os
from dotenv import load_dotenv

load_dotenv()

# APIs
ODDS_API_KEY        = os.getenv("ODDS_API_KEY")
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID")
TENNIS_API_KEY      = os.getenv("TENNIS_API_KEY")   # api-tennis.com (opcional)

# Deportes activos
SPORTS_FOOTBALL = ["soccer_spain_la_liga", "soccer_epl"]
SPORTS_TENNIS = ["tennis_atp_barcelona_open", "tennis_atp_munich", "tennis_wta_stuttgart_open"]
SPORTS = SPORTS_FOOTBALL + SPORTS_TENNIS

# Superficie por torneo (para el modelo de tenis)
TOURNAMENT_SURFACE = {
    "tennis_atp_barcelona_open": "Clay",
    "tennis_atp_munich":         "Clay",
    "tennis_wta_stuttgart_open": "Clay",
    "tennis_atp_madrid_open":    "Clay",
    "tennis_wta_madrid_open":    "Clay",
    "tennis_atp_italian_open":   "Clay",
    "tennis_wta_italian_open":   "Clay",
}

# Circuito por torneo
TOURNAMENT_TOUR = {
    "tennis_atp_barcelona_open": "ATP",
    "tennis_atp_munich":         "ATP",
    "tennis_wta_stuttgart_open": "WTA",
    "tennis_atp_madrid_open":    "ATP",
    "tennis_wta_madrid_open":    "WTA",
    "tennis_atp_italian_open":   "ATP",
    "tennis_wta_italian_open":   "WTA",
}

# Bankroll inicial
BANKROLL = 1000

# Casas
BOOKMAKERS = ["betfair", "bet365", "williamhill", "bwin"]

# Valor
MIN_EV_THRESHOLD = 0.03
KELLY_FRACTION = 0.25
MAX_STAKE_EUR = 50
# Descuento aplicado a la cuota antes de calcular EV para compensar movimiento
# entre ejecución del modelo y momento real de la apuesta (por defecto 2.5 %)
ODD_DISCOUNT = 0.025

# Pesos del modelo (deben sumar 1.0)
SERVE_WEIGHT = 0.25   # modelo jerárquico punto→game→set→partido
FORM_WEIGHT  = 0.15   # racha reciente (últimos 20 partidos, decay exponencial)
RANK_WEIGHT  = 0.50   # Bradley-Terry con puntos de ranking (datos algo obsoletos)
H2H_WEIGHT   = 0.10   # historial directo entre los dos jugadores

# EV máximo aceptable (picks con EV > MAX_EV se consideran errores del modelo)
MAX_EV = 0.30

# URLs
BASE_URL_ODDS = "https://api.the-odds-api.com/v4"