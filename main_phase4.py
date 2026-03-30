"""
HomeStation - Raspberry Pi Pico 2W  (Phase 4+: icons + polished UI)
MQTT subscriber + OLED smart display with weather icons, status bar, page dots.

Hardware: Waveshare 1.3" OLED (SH1106, 128x64)
  DC=GP8, CS=GP9, CLK=GP10, DIN=GP11, RES=GP12
  Button A = GP15 (cycle pages), Button B = GP17 (cycle cities)

Files needed on Pico:
  main.py   ← this file
  icons.py  ← pixel bitmaps
  sh1106.py ← https://github.com/robert-hh/SH1106/blob/master/sh1106.py
"""

import gc
import json
import time
import network
import machine
import framebuf
from machine import Pin, SPI
from umqtt.simple import MQTTClient
import sh1106
import icons

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

WIFI_SSID   = "YOUR_WIFI_SSID"
WIFI_PASS   = "YOUR_WIFI_PASSWORD"
MQTT_BROKER = "10.0.0.X"       # ← Pi 4 local IP
MQTT_PORT   = 1883
MQTT_BASE   = "homestation"
CLIENT_ID   = "pico-display"

CITIES      = ["edmonton", "tokyo", "vancouver"]
CITY_LABELS = ["Edmonton", "Tokyo", "Vancouver"]
PAGE_COUNT  = 4   # weather, forecast, aqi, indoor

# ─────────────────────────────────────────────
# DISPLAY INIT
# ─────────────────────────────────────────────

WIDTH  = 128
HEIGHT = 64

spi = SPI(1,
    baudrate=10_000_000,
    polarity=0, phase=0,
    sck=Pin(10), mosi=Pin(11)
)
dc  = Pin(8,  Pin.OUT)
cs  = Pin(9,  Pin.OUT)
rst = Pin(12, Pin.OUT)

oled = sh1106.SH1106_SPI(WIDTH, HEIGHT, spi, dc, rst, cs, rotate=0)
oled.fill(0)
oled.show()

# ─────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────

state = {
    "page":       0,
    "city":       0,
    "data":       {},
    "last_press": 0,
    "dirty":      True,
}

def default_weather():
    return {"temp": "--", "desc": "No data", "humidity": "--",
            "feels_like": "--", "wind_kph": "--", "icon": "02d"}

def default_forecast():
    return {"items": [{"time": "--:--", "temp": "--", "desc": "—",
                        "icon": "02d"} for _ in range(4)]}

def default_aqi():
    return {"aqi": "--", "category": "No data", "pm2_5": "--", "pm10": "--"}

def default_indoor():
    return {"temp": "--", "humidity": "--", "pressure": "--", "source": "—"}

for city in CITIES:
    state["data"][f"weather/{city}"]  = default_weather()
    state["data"][f"forecast/{city}"] = default_forecast()
    state["data"][f"aqi/{city}"]      = default_aqi()
state["data"]["sensors/indoor"] = default_indoor()

# ─────────────────────────────────────────────
# DRAWING PRIMITIVES
# ─────────────────────────────────────────────

def cls():
    oled.fill(0)

def t(s, x, y, col=1):
    oled.text(str(s), x, y, col)

def hline(y, x0=0, w=WIDTH, col=1):
    oled.hline(x0, y, w, col)

def vline(x, y0, h, col=1):
    oled.vline(x, y0, h, col)

def show():
    oled.show()

def center_x(s, y, col=1):
    x = max(0, (WIDTH - len(str(s)) * 8) // 2)
    t(s, x, y, col)

def draw_icon(icon_code, x, y):
    fb = icons.get_icon_fb(icon_code)
    oled.blit(fb, x, y)

def draw_progress_bar(x, y, w, h, frac, col=1):
    oled.rect(x, y, w, h, col)
    fw = int(w * max(0.0, min(1.0, frac)))
    if fw > 2:
        oled.fill_rect(x + 1, y + 1, fw - 2, h - 2, col)

def draw_page_dots(current, total, y=59):
    """Small dot indicators at bottom center."""
    dot_w   = 5
    spacing = 7
    total_w = total * spacing
    x_start = (WIDTH - total_w) // 2
    for i in range(total):
        x = x_start + i * spacing
        if i == current:
            oled.fill_rect(x, y, dot_w, 5, 1)
        else:
            oled.rect(x, y, dot_w, 5, 1)

def header(left, right="", invert=True):
    """Draw a filled header bar with left + right text."""
    oled.fill_rect(0, 0, WIDTH, 11, 1 if invert else 0)
    t(left[:10], 2, 2, 0 if invert else 1)
    if right:
        rx = WIDTH - len(right) * 8 - 2
        t(right[:8], max(rx, 60), 2, 0 if invert else 1)

# ─────────────────────────────────────────────
# SPLASH / STATUS SCREENS
# ─────────────────────────────────────────────

def splash():
    cls()
    center_x("HomeStation", 10)
    hline(22)
    center_x("Smart Display", 28)
    center_x("v1.0", 40)
    show()

def connecting_screen(label, detail=""):
    cls()
    center_x("Connecting...", 14)
    hline(26)
    center_x(label, 32)
    if detail:
        center_x(detail[:16], 44)
    show()

def error_screen(msg1, msg2=""):
    cls()
    oled.fill_rect(0, 0, WIDTH, 11, 1)
    t("  ERROR", 2, 2, 0)
    center_x(msg1, 22)
    if msg2:
        center_x(msg2, 36)
    show()

# ─────────────────────────────────────────────
# PAGE: WEATHER
# ─────────────────────────────────────────────

def page_weather():
    city  = CITIES[state["city"]]
    label = CITY_LABELS[state["city"]]
    d     = state["data"].get(f"weather/{city}", default_weather())

    cls()
    header("WEATHER", label)

    # Icon top-right
    draw_icon(d.get("icon", "02d"), 108, 13)

    # Large temperature
    temp_s = f"{d['temp']}\xb0C"   # °C
    t(temp_s, 2, 14)

    # Description — truncate to avoid overflow
    desc = str(d["desc"])[:14]
    t(desc, 2, 26)

    # Divider
    hline(37)

    # Two-column stats
    t(f"Hum {d['humidity']}%", 2, 40)
    t(f"FL {d['feels_like']}\xb0", 68, 40)
    t(f"Wind {d['wind_kph']}kph", 2, 50)

    draw_page_dots(state["page"], PAGE_COUNT)
    show()

# ─────────────────────────────────────────────
# PAGE: FORECAST
# ─────────────────────────────────────────────

def page_forecast():
    city  = CITIES[state["city"]]
    label = CITY_LABELS[state["city"]]
    d     = state["data"].get(f"forecast/{city}", default_forecast())
    items = d.get("items", [])

    cls()
    header("FORECAST", label)

    # Column headers
    t("Time  Tmp  Cond", 0, 13)
    hline(22)

    for i, item in enumerate(items[:4]):
        y    = 24 + i * 9
        time_s = str(item.get("time", "--:--"))[:5]
        temp_s = f"{item.get('temp', '--'):>3}\xb0"
        desc_s = str(item.get("desc", ""))[:6]
        t(f"{time_s} {temp_s} {desc_s}", 0, y)

    draw_page_dots(state["page"], PAGE_COUNT)
    show()

# ─────────────────────────────────────────────
# PAGE: AQI
# ─────────────────────────────────────────────

AQI_BAR_COLORS = ["", "Good", "Fair", "Moderate", "Poor", "V.Poor"]

def page_aqi():
    city  = CITIES[state["city"]]
    label = CITY_LABELS[state["city"]]
    d     = state["data"].get(f"aqi/{city}", default_aqi())

    aqi_val = d.get("aqi", "--")
    cat     = str(d.get("category", ""))

    cls()
    header("AIR QUALITY", label)

    center_x(f"Index: {aqi_val}", 16)
    center_x(cat, 26)

    # Visual AQI scale bar (1–5)
    hline(36)
    t("1", 2, 40)
    t("2", 28, 40)
    t("3", 54, 40)
    t("4", 80, 40)
    t("5", 106, 40)
    for i in range(5):
        x = 2 + i * 26
        filled = isinstance(aqi_val, int) and i < aqi_val
        if filled:
            oled.fill_rect(x, 50, 20, 7, 1)
        else:
            oled.rect(x, 50, 20, 7, 1)

    draw_page_dots(state["page"], PAGE_COUNT)
    show()

# ─────────────────────────────────────────────
# PAGE: INDOOR SENSORS
# ─────────────────────────────────────────────

def page_indoor():
    d = state["data"].get("sensors/indoor", default_indoor())

    cls()
    header("INDOOR", "Sensors")

    # Icons + values
    draw_icon("thermo", 4, 14)   # will fall back gracefully
    t(f"{d['temp']}\xb0C", 24, 18)

    draw_icon("droplet", 4, 32)
    t(f"{d['humidity']}% RH", 24, 36)

    hline(47)
    t(f"Pressure: {d['pressure']} hPa", 2, 50)

    draw_page_dots(state["page"], PAGE_COUNT)
    show()

# ─────────────────────────────────────────────
# PAGE DISPATCH
# ─────────────────────────────────────────────

RENDERERS = [page_weather, page_forecast, page_aqi, page_indoor]

def redraw():
    RENDERERS[state["page"]]()
    state["dirty"] = False

# ─────────────────────────────────────────────
# BUTTONS
# ─────────────────────────────────────────────

btn_a = Pin(15, Pin.IN, Pin.PULL_UP)  # cycle pages
btn_b = Pin(17, Pin.IN, Pin.PULL_UP)  # cycle cities

DEBOUNCE_MS = 200

def check_buttons():
    now = time.ticks_ms()
    if time.ticks_diff(now, state["last_press"]) < DEBOUNCE_MS:
        return

    if btn_a.value() == 0:
        state["page"]       = (state["page"] + 1) % PAGE_COUNT
        state["last_press"] = now
        state["dirty"]      = True

    elif btn_b.value() == 0:
        state["city"]       = (state["city"] + 1) % len(CITIES)
        state["last_press"] = now
        state["dirty"]      = True

# ─────────────────────────────────────────────
# WI-FI
# ─────────────────────────────────────────────

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return True
    connecting_screen("Wi-Fi", WIFI_SSID[:16])
    wlan.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(30):
        if wlan.isconnected():
            return True
        time.sleep(0.5)
    return False

# ─────────────────────────────────────────────
# MQTT
# ─────────────────────────────────────────────

mqtt_client     = None
last_reconnect  = 0
RECONNECT_DELAY = 10_000  # ms

def on_message(topic, msg):
    topic_str = topic.decode()
    key = topic_str[len(MQTT_BASE) + 1:] if topic_str.startswith(MQTT_BASE + "/") else topic_str
    try:
        state["data"][key] = json.loads(msg)
        state["dirty"]     = True
    except Exception as e:
        print(f"[MQTT] JSON error on {key}: {e}")

def connect_mqtt():
    global mqtt_client
    connecting_screen("MQTT", MQTT_BROKER)
    try:
        mqtt_client = MQTTClient(CLIENT_ID, MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.set_callback(on_message)
        mqtt_client.connect()
        for city in CITIES:
            mqtt_client.subscribe(f"{MQTT_BASE}/weather/{city}")
            mqtt_client.subscribe(f"{MQTT_BASE}/forecast/{city}")
            mqtt_client.subscribe(f"{MQTT_BASE}/aqi/{city}")
        mqtt_client.subscribe(f"{MQTT_BASE}/sensors/indoor")
        print("[MQTT] Connected and subscribed")
        return True
    except Exception as e:
        print(f"[MQTT] Connect failed: {e}")
        mqtt_client = None
        return False

def mqtt_tick():
    global mqtt_client, last_reconnect
    if mqtt_client is None:
        now = time.ticks_ms()
        if time.ticks_diff(now, last_reconnect) > RECONNECT_DELAY:
            last_reconnect = now
            connect_mqtt()
        return
    try:
        mqtt_client.check_msg()
    except Exception as e:
        print(f"[MQTT] Error: {e} — will reconnect")
        mqtt_client = None

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    splash()
    time.sleep(1)

    if not connect_wifi():
        error_screen("WiFi failed", WIFI_SSID[:12])
        return

    if not connect_mqtt():
        error_screen("MQTT failed", MQTT_BROKER)
        return

    redraw()
    print("[HomeStation] Running")

    while True:
        check_buttons()
        mqtt_tick()
        if state["dirty"]:
            redraw()
        gc.collect()
        time.sleep_ms(50)

main()
