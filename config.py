"""
config.py — HomeStation Pico configuration
Copy to Pico root alongside main.py
"""

WIFI_SSID   = "Salar"
WIFI_PASS   = "zpqu3533"
MQTT_BROKER = "10.210.165.230"      # Pi 4 local IP address
MQTT_PORT   = 1883
MQTT_BASE   = "homestation"
CLIENT_ID   = "pico-display"

CITIES      = ["edmonton", "tokyo", "vancouver"]
CITY_LABELS = ["Edmonton", "Tokyo", "Vancouver"]
