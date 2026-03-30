"""
HomeStation - Raspberry Pi 4 Data Hub
Fetches weather/AQI, reads sensors, publishes to MQTT.

Dependencies:
  pip install paho-mqtt requests adafruit-circuitpython-dht adafruit-circuitpython-bmp180
  sudo apt install libgpiod2
"""

import time
from datetime import datetime, timezone
import json
import logging
import threading
import requests
import paho.mqtt.client as mqtt
from transit import fetch_transit_data


# Optional sensor imports — comment out if hardware not connected
import board
import adafruit_dht

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
    ("Calgary",   "Calgary,CA",   51.0447, -114.0719),
    ("Shiraz",    "Shiraz,IR",    29.5918,   52.5837),
    ("Tokyo",     "Tokyo,JP",     35.6762,  139.6503),
]

# Sensor GPIO pins (BCM numbering)
DHT11_PIN     = 4    # GPIO4

# How often to fetch/publish (seconds)
WEATHER_INTERVAL = 600   # 10 min
SENSOR_INTERVAL  = 30    # 30 sec

TRANSIT_INTERVAL = 60

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

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pi4-hub")
def mqtt_connect():
    def on_connect(c, userdata, flags, rc, properties=None):
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
def transit_loop():
    while True:
        log.info("=== Fetching transit data ===")
        try:
            data = fetch_transit_data()
            publish("transit/edmonton", data)
        except Exception as e:
            log.error(f"Transit fetch error: {e}")
        time.sleep(TRANSIT_INTERVAL)
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

# WMO weather code ? human description
WMO_CODES = {
    0: "Clear Sky", 1: "Mostly Clear", 2: "Partly Cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy Fog",
    51: "Light Drizzle", 53: "Drizzle", 55: "Heavy Drizzle",
    61: "Light Rain", 63: "Rain", 65: "Heavy Rain",
    71: "Light Snow", 73: "Snow", 75: "Heavy Snow", 77: "Snow Grains",
    80: "Rain Showers", 81: "Showers", 82: "Heavy Showers",
    85: "Snow Showers", 86: "Heavy Snow Showers",
    95: "Thunderstorm", 96: "Thunderstorm w Hail", 99: "Thunderstorm w Hail",
}

def wmo_to_icon(code: int) -> str:
    """Map WMO code to OWM-style icon string (used by Pico icon renderer)."""
    if code == 0:                    return "01d"
    if code in (1, 2):               return "02d"
    if code == 3:                    return "03d"
    if code in (45, 48):             return "50d"
    if code in (51, 53, 55, 61, 63): return "10d"
    if code in (65, 80, 81, 82):     return "09d"
    if code in (71, 73, 75, 77, 85, 86): return "13d"
    if code in (95, 96, 99):         return "11d"
    return "02d"

def eaqi_category(aqi: int) -> str:
    if aqi <= 20:  return "Good"
    if aqi <= 40:  return "Fair"
    if aqi <= 60:  return "Moderate"
    if aqi <= 80:  return "Poor"
    if aqi <= 100: return "Very Poor"
    return "Hazardous"

def fetch_weather_and_forecast(lat: float, lon: float) -> dict | None:
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude":        lat,
            "longitude":       lon,
            "current":         "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,apparent_temperature",
            "daily":           "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max",
            "wind_speed_unit": "kmh",
            "forecast_days":   3,
            "timezone":        "auto",
        }
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        d = r.json()
        c = d["current"]

        weather = {
            "temp":       round(c["temperature_2m"]),
            "feels_like": round(c["apparent_temperature"]),
            "humidity":   c["relative_humidity_2m"],
            "desc":       WMO_CODES.get(c["weather_code"], "Unknown"),
            "icon":       wmo_to_icon(c["weather_code"]),
            "wind_kph":   round(c["wind_speed_10m"], 1),
            "timestamp":  int(time.time()),
        }

        # Skip today (index 0), take next 2 days
        daily = d["daily"]
        forecast_items = []
        for i in range(1, 3):
            date     = daily["time"][i]             # "2025-03-29"
            month_day = date[5:]                    # "03-29"
            forecast_items.append({
                "date":      month_day,
                "high":      round(daily["temperature_2m_max"][i]),
                "low":       round(daily["temperature_2m_min"][i]),
                "desc":      WMO_CODES.get(daily["weather_code"][i], "")[:12],
                "icon":      wmo_to_icon(daily["weather_code"][i]),
                "precip_pct": daily["precipitation_probability_max"][i],
            })

        return {"weather": weather, "forecast": {"items": forecast_items}}
    except Exception as e:
        log.error(f"Open-Meteo fetch failed: {e}")
        return None


def fetch_aqi(lat: float, lon: float) -> dict | None:
    """Open-Meteo air quality API also free, no key."""
    try:
        url = "https://air-quality-api.open-meteo.com/v1/air-quality"
        params = {
            "latitude":  lat,
            "longitude": lon,
            "current":   "european_aqi,pm10,pm2_5,carbon_monoxide",
        }
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        d = r.json()
        c = d["current"]
        aqi = c.get("european_aqi", 0)
        return {
            "aqi":      aqi,
            "category": eaqi_category(aqi),
            "pm2_5":    round(c.get("pm2_5", 0), 1),
            "pm10":     round(c.get("pm10",  0), 1),
            "co":       round(c.get("carbon_monoxide", 0), 1),
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
    global dht_sensor
    try:
        dht_sensor = adafruit_dht.DHT11(board.D4)
        log.info("DHT11 initialized")
    except Exception as e:
        log.error(f"DHT11 init failed: {e}")

def read_sensors() -> dict:
    for attempt in range(5):
        try:
            return {
                "temp":      dht_sensor.temperature,
                "humidity":  dht_sensor.humidity,
                "pressure":  None,
                "source":    "dht11",
                "timestamp": int(time.time()),
            }
        except RuntimeError as e:
            log.debug(f"DHT11 read error (attempt {attempt+1}): {e}")
            time.sleep(2)
    log.warning("DHT11 failed after 5 attempts")
    return {"temp": None, "humidity": None, "pressure": None, "source": "error"}

# ─────────────────────────────────────────────
# PUBLISH LOOPS
# ─────────────────────────────────────────────

def weather_loop():
    while True:
        log.info("=== Fetching weather data ===")
        for display_name, city_name, lat, lon in CITIES:
            slug = display_name.lower()
            result = fetch_weather_and_forecast(lat, lon)
            if result:
                publish(f"weather/{slug}", result["weather"])
                publish(f"forecast/{slug}", result["forecast"])
            aqi = fetch_aqi(lat, lon)
            if aqi:
                publish(f"aqi/{slug}", aqi)
            time.sleep(1)
        log.info(f"Weather cycle done next in {WEATHER_INTERVAL}s")
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
def transit_loop():
    while True:
        log.info("=== Fetching transit data ===")
        try:
            from transit import _fetch_trip_updates, _load_static_gtfs, _static_cache
            _load_static_gtfs()
            feed = _fetch_trip_updates()
            jubilee_ids = _static_cache["jubilee_ids"]
            lrt_route_ids = {
                v for k, v in _static_cache["route_ids"].items()
                if k in ("Capital", "Metro", "Valley")
            }
            log.info(f"LRT route IDs: {lrt_route_ids}")
            log.info(f"Jubilee stop IDs: {jubilee_ids}")

            # Scan entire feed for anything hitting jubilee stops
            for entity in feed.entity:
                if not entity.HasField("trip_update"):
                    continue
                tu = entity.trip_update
                for stu in tu.stop_time_update:
                    if str(stu.stop_id) in jubilee_ids:
                        tid      = tu.trip.trip_id
                        rid      = tu.trip.route_id  # from the feed directly
                        rid_look = _static_cache["trips"].get(tid, "NOT IN TRIPS")
                        head     = _static_cache["headsigns"].get(tid, "NO HEADSIGN")
                        log.info(f"  HIT stop={stu.stop_id} trip={tid} route_in_feed={rid} route_lookup={rid_look} head='{head}'")

            data = fetch_transit_data()
            publish("transit/edmonton", data)
            sample_stops = set()
            for entity in feed.entity:
                if not entity.HasField("trip_update"):
                    continue
                for stu in entity.trip_update.stop_time_update:
                    sample_stops.add(str(stu.stop_id))
            log.info(f"All stop IDs in feed (first 20): {list(sample_stops)[:20]}")
            log.info(f"Total entities in feed: {len(feed.entity)}")
        except Exception as e:
            log.error(f"Transit fetch error: {e}")
        time.sleep(TRANSIT_INTERVAL)
def main():
    log.info("HomeStation Pi4 Hub starting...")
    mqtt_connect()
    init_sensors()

    # Give MQTT a moment to connect
    time.sleep(2)

    # Run both loops in background threads
    t_weather = threading.Thread(target=weather_loop, daemon=True)
    t_sensors = threading.Thread(target=sensor_loop,  daemon=True)
    t_transit = threading.Thread(target=transit_loop, daemon=True)

    
    t_weather.start()
    t_sensors.start()
    t_transit.start()

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