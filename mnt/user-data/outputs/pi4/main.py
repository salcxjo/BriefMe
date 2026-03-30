"""
HomeStation - Raspberry Pi 4 Data Hub
Fetches weather/AQI, reads sensors, publishes to MQTT.

Dependencies:
  pip install paho-mqtt requests adafruit-circuitpython-dht adafruit-circuitpython-bmp180
  sudo apt install libgpiod2
"""

import time
import json
import logging
import threading
import requests
import paho.mqtt.client as mqtt

# Optional sensor imports — comment out if hardware not connected
try:
    import board
    import adafruit_dht
    import adafruit_bmp180
    import busio
    SENSORS_AVAILABLE = True
except ImportError:
    SENSORS_AVAILABLE = False
    print("[WARN] Sensor libraries not found — running in mock mode")

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

MQTT_BROKER   = "localhost"
MQTT_PORT     = 1883
MQTT_BASE     = "homestation"

OWM_API_KEY   = "YOUR_OPENWEATHERMAP_API_KEY"  # <-- replace this
OWM_BASE_URL  = "https://api.openweathermap.org/data/2.5"
OWM_AIR_URL   = "https://api.openweathermap.org/data/2.5/air_pollution"

# Cities to track — (display name, OWM city name, lat, lon)
CITIES = [
    ("Edmonton",  "Edmonton,CA",  53.5461, -113.4938),
    ("Tokyo",     "Tokyo,JP",     35.6762,  139.6503),
    ("Vancouver", "Vancouver,CA", 49.2827, -123.1207),
]

# Sensor GPIO pins (BCM numbering)
DHT11_PIN     = 4    # GPIO4
BMP180_SDA    = 2    # I2C SDA (GPIO2)
BMP180_SCL    = 3    # I2C SCL (GPIO3)

# How often to fetch/publish (seconds)
WEATHER_INTERVAL = 600   # 10 min
SENSOR_INTERVAL  = 30    # 30 sec

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("homestation")

# ─────────────────────────────────────────────
# MQTT CLIENT
# ─────────────────────────────────────────────

client = mqtt.Client(client_id="pi4-hub")

def mqtt_connect():
    def on_connect(c, userdata, flags, rc):
        if rc == 0:
            log.info("MQTT connected to broker")
        else:
            log.error(f"MQTT connection failed: rc={rc}")

    def on_disconnect(c, userdata, rc):
        log.warning("MQTT disconnected — retrying in 5s")
        time.sleep(5)
        try:
            c.reconnect()
        except Exception as e:
            log.error(f"Reconnect failed: {e}")

    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_start()

def publish(topic, payload: dict):
    full_topic = f"{MQTT_BASE}/{topic}"
    message    = json.dumps(payload)
    result     = client.publish(full_topic, message, retain=True)
    if result.rc == mqtt.MQTT_ERR_SUCCESS:
        log.info(f"  → {full_topic}: {message[:80]}")
    else:
        log.error(f"  ✗ publish failed on {full_topic}")

# ─────────────────────────────────────────────
# WEATHER FETCHER
# ─────────────────────────────────────────────

AQI_CATEGORIES = {
    1: "Good",
    2: "Fair",
    3: "Moderate",
    4: "Poor",
    5: "Very Poor",
}

def fetch_weather(city_name: str) -> dict | None:
    try:
        url    = f"{OWM_BASE_URL}/weather"
        params = {"q": city_name, "appid": OWM_API_KEY, "units": "metric"}
        r      = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        d = r.json()
        return {
            "temp":       round(d["main"]["temp"]),
            "feels_like": round(d["main"]["feels_like"]),
            "humidity":   d["main"]["humidity"],
            "desc":       d["weather"][0]["description"].title(),
            "icon":       d["weather"][0]["icon"],        # e.g. "01d"
            "wind_kph":   round(d["wind"]["speed"] * 3.6, 1),
            "timestamp":  int(time.time()),
        }
    except Exception as e:
        log.error(f"Weather fetch failed for {city_name}: {e}")
        return None

def fetch_forecast(city_name: str) -> list | None:
    """Returns next 4 forecast periods (3h intervals → ~12h ahead)."""
    try:
        url    = f"{OWM_BASE_URL}/forecast"
        params = {"q": city_name, "appid": OWM_API_KEY, "units": "metric", "cnt": 4}
        r      = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        d = r.json()
        return [
            {
                "time":  item["dt_txt"][11:16],          # "HH:MM"
                "temp":  round(item["main"]["temp"]),
                "desc":  item["weather"][0]["description"].title(),
                "icon":  item["weather"][0]["icon"],
            }
            for item in d["list"]
        ]
    except Exception as e:
        log.error(f"Forecast fetch failed for {city_name}: {e}")
        return None

def fetch_aqi(lat: float, lon: float) -> dict | None:
    try:
        params = {"lat": lat, "lon": lon, "appid": OWM_API_KEY}
        r      = requests.get(OWM_AIR_URL, params=params, timeout=10)
        r.raise_for_status()
        d   = r.json()
        aqi = d["list"][0]["main"]["aqi"]
        components = d["list"][0]["components"]
        return {
            "aqi":      aqi,
            "category": AQI_CATEGORIES.get(aqi, "Unknown"),
            "pm2_5":    round(components.get("pm2_5", 0), 1),
            "pm10":     round(components.get("pm10", 0), 1),
            "co":       round(components.get("co", 0), 1),
        }
    except Exception as e:
        log.error(f"AQI fetch failed: {e}")
        return None

# ─────────────────────────────────────────────
# SENSOR READER
# ─────────────────────────────────────────────

dht_sensor  = None
bmp_sensor  = None

def init_sensors():
    global dht_sensor, bmp_sensor
    if not SENSORS_AVAILABLE:
        return
    try:
        dht_sensor = adafruit_dht.DHT11(board.D4)
        log.info("DHT11 initialized")
    except Exception as e:
        log.warning(f"DHT11 init failed: {e}")

    try:
        i2c        = busio.I2C(board.SCL, board.SDA)
        bmp_sensor = adafruit_bmp180.Adafruit_BMP180_I2C(i2c)
        log.info("BMP180 initialized")
    except Exception as e:
        log.warning(f"BMP180 init failed: {e}")

def read_sensors() -> dict:
    """Read available sensors; fall back to None for missing values."""
    data = {
        "temp":     None,
        "humidity": None,
        "pressure": None,
        "source":   "mock",
    }

    if not SENSORS_AVAILABLE:
        # Mock data for development without hardware
        import random
        data.update({
            "temp":     round(20 + random.uniform(-2, 2), 1),
            "humidity": round(45 + random.uniform(-5, 5), 1),
            "pressure": round(1013 + random.uniform(-5, 5), 1),
            "source":   "mock",
        })
        return data

    # DHT11
    if dht_sensor:
        try:
            data["temp"]     = dht_sensor.temperature
            data["humidity"] = dht_sensor.humidity
            data["source"]   = "dht11"
        except RuntimeError as e:
            log.debug(f"DHT11 read error (normal): {e}")

    # BMP180 — prefer its temp if available; always get pressure
    if bmp_sensor:
        try:
            data["pressure"] = round(bmp_sensor.pressure, 1)
            if data["temp"] is None:
                data["temp"] = round(bmp_sensor.temperature, 1)
            data["source"] = "dht11+bmp180" if data["humidity"] else "bmp180"
        except Exception as e:
            log.debug(f"BMP180 read error: {e}")

    data["timestamp"] = int(time.time())
    return data

# ─────────────────────────────────────────────
# PUBLISH LOOPS
# ─────────────────────────────────────────────

def weather_loop():
    """Fetch + publish weather/forecast/AQI for all cities on a timer."""
    while True:
        log.info("=== Fetching weather data ===")
        for display_name, city_name, lat, lon in CITIES:
            slug = display_name.lower()

            weather = fetch_weather(city_name)
            if weather:
                publish(f"weather/{slug}", weather)

            forecast = fetch_forecast(city_name)
            if forecast:
                publish(f"forecast/{slug}", {"items": forecast})

            aqi = fetch_aqi(lat, lon)
            if aqi:
                publish(f"aqi/{slug}", aqi)

            time.sleep(1)  # small gap between API calls

        log.info(f"Weather cycle done — next in {WEATHER_INTERVAL}s")
        time.sleep(WEATHER_INTERVAL)

def sensor_loop():
    """Read local sensors and publish on a short interval."""
    while True:
        data = read_sensors()
        publish("sensors/indoor", data)
        log.debug(f"Sensors: {data}")
        time.sleep(SENSOR_INTERVAL)

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    log.info("HomeStation Pi4 Hub starting...")
    mqtt_connect()
    init_sensors()

    # Give MQTT a moment to connect
    time.sleep(2)

    # Run both loops in background threads
    t_weather = threading.Thread(target=weather_loop, daemon=True)
    t_sensors = threading.Thread(target=sensor_loop,  daemon=True)
    t_weather.start()
    t_sensors.start()

    log.info("All loops running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down...")
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()
