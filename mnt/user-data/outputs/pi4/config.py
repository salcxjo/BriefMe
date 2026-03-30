"""
config.py — HomeStation Pi 4 configuration
Keep this file private (add to .gitignore)
"""

# ── MQTT ──────────────────────────────────────────────────────────────────────
MQTT_BROKER  = "localhost"
MQTT_PORT    = 1883
MQTT_BASE    = "homestation"

# ── API ───────────────────────────────────────────────────────────────────────
OWM_API_KEY  = "YOUR_OPENWEATHERMAP_API_KEY"

# ── Cities ────────────────────────────────────────────────────────────────────
# (display_name, owm_city_string, latitude, longitude)
CITIES = [
    ("Edmonton",  "Edmonton,CA",  53.5461, -113.4938),
    ("Tokyo",     "Tokyo,JP",     35.6762,  139.6503),
    ("Vancouver", "Vancouver,CA", 49.2827, -123.1207),
]

# ── Intervals (seconds) ───────────────────────────────────────────────────────
WEATHER_INTERVAL = 600   # 10 min
SENSOR_INTERVAL  = 30    # 30 sec

# ── Sensors ───────────────────────────────────────────────────────────────────
DHT11_GPIO = 4    # BCM pin number
