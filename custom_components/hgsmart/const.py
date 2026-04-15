"""Constants for the HGSmart Pet Feeder integration."""
DOMAIN = "hgsmart"

# API Configuration
BASE_URL = "https://hgsmart.net/hsapi"
CLIENT_ID = "r3ptinrmmsl9rnlis6yf"
CLIENT_SECRET = "ss9Ytzb4gSceaPhwhKteAPLiVP4pmU8zxLEcWuscM6Vsnj7wMt"

# Configuration
CONF_UPDATE_INTERVAL = "update_interval"
CONF_REFRESH_TOKEN = "refresh_token"

# Default settings
DEFAULT_UPDATE_INTERVAL = 15

# Schedule configuration
SCHEDULE_SLOTS = 6  # Slots numbered 0-5
MIN_PORTIONS = 1
MAX_PORTIONS = 9

# Meal call sound (device attribute identifiers: music, choosevoice)
MEAL_CALL_OPTION_DEFAULT = "Default"
MEAL_CALL_OPTION_CUSTOM = "Custom Recording"
ATTR_CHOOSEVOICE = "choosevoice"
ATTR_CHILD = "child"  # button lockout (0=off, 1=on)
