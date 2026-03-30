"""
HomeStation - Raspberry Pi Pico 2W  (Final: Phases 3-5)
MQTT display + weather icons + polished UI + advanced buttons

Files needed on Pico root:
  main.py   ← this file
  icons.py
  buttons.py
  sh1106.py ← https://github.com/robert-hh/SH1106/blob/master/sh1106.py
"""

import gc
import json
import time
import network
import framebuf
from machine import Pin, SPI
from umqtt.simple import MQTTClient
import sh1106
import icons
from buttons import ButtonHandler

# ─────────────────────────────────────────────
# CONFIG  — edit before flashing
# ─────────────────────────────────────────────

WIFI_SSID   = "YOUR_WIFI_SSID"
WIFI_PASS   = "YOUR_WIFI_PASSWORD"
MQTT_BROKER = "10.0.0.X"      # Pi 4 local IP
MQTT_PORT   = 1883
MQTT_BASE   = "homestation"
CLIENT_ID   = "pico-display"

CITIES      = ["edmonton", "tokyo", "vancouver"]
CITY_LABELS = ["Edmonton", "Tokyo", "Vancouver"]
PAGE_COUNT  = 4   # weather / forecast / aqi / indoor

# How long to show the HUD overlay on button press
HUD_DURATION_MS = 800

# ─────────────────────────────────────────────
# DISPLAY
# ─────────────────────────────────────────────

WIDTH  = 128
HEIGHT = 64

spi = SPI(1, baudrate=10_000_000, polarity=0, phase=0,
          sck=Pin(10), mosi=Pin(11))
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
    "page":        0,
    "city":        0,
    "data":        {},
    "dirty":       True,
    "hud_until":   0,    # ticks_ms when HUD overlay expires
}

PAGE_NAMES = ["Weather", "Forecast", "AQI", "Indoor"]

def _w():
    return {"temp": "--", "desc": "No data", "humidity": "--",
            "feels_like": "--", "wind_kph": "--", "icon": "02d"}
def _f():
    return {"items": [{"time": "--:--", "temp": "--",
                        "desc": "—", "icon": "02d"} for _ in range(4)]}
def _a():
    return {"aqi": "--", "category": "No data", "pm2_5": "--", "pm10": "--"}
def _i():
    return {"temp": "--", "humidity": "--", "pressure": "--", "source": "—"}

for _c in CITIES:
    state["data"][f"weather/{_c}"]  = _w()
    state["data"][f"forecast/{_c}"] = _f()
    state["data"][f"aqi/{_c}"]      = _a()
state["data"]["sensors/indoor"] = _i()

# ─────────────────────────────────────────────
# DRAWING HELPERS
# ─────────────────────────────────────────────

def cls():                          oled.fill(0)
def show():                         oled.show()
def t(s, x, y, col=1):             oled.text(str(s), x, y, col)
def hline(y, col=1):               oled.hline(0, y, WIDTH, col)

def cx(s, y, col=1):
    x = max(0, (WIDTH - len(str(s)) * 8) // 2)
    t(s, x, y, col)

def header(left, right=""):
    oled.fill_rect(0, 0, WIDTH, 11, 1)
    t(left[:10], 2, 2, 0)
    if right:
        t(right[:8], WIDTH - len(right[:8]) * 8 - 2, 2, 0)

def draw_icon(code, x, y):
    oled.blit(icons.get_icon_fb(code), x, y)

def page_dots(cur, total, y=59):
    sp  = 7
    x0  = (WIDTH - total * sp) // 2
    for i in range(total):
        x = x0 + i * sp
        if i == cur:
            oled.fill_rect(x, y, 5, 5, 1)
        else:
            oled.rect(x, y, 5, 5, 1)

# ─────────────────────────────────────────────
# HUD OVERLAY
# ─────────────────────────────────────────────

def show_hud():
    """Briefly overlay current page + city info."""
    p = PAGE_NAMES[state["page"]]
    c = CITY_LABELS[state["city"]]
    cls()
    oled.fill_rect(16, 12, 96, 40, 0)
    oled.rect(16, 12, 96, 40, 1)
    cx(p, 20)
    hline(33)
    cx(c, 37)
    show()
    state["hud_until"] = time.ticks_ms() + HUD_DURATION_MS

def hud_active():
    return time.ticks_diff(state["hud_until"], time.ticks_ms()) > 0

# ─────────────────────────────────────────────
# PAGE RENDERERS
# ─────────────────────────────────────────────

def page_weather():
    city  = CITIES[state["city"]]
    label = CITY_LABELS[state["city"]]
    d     = state["data"].get(f"weather/{city}", _w())
    cls()
    header("WEATHER", label)
    draw_icon(d.get("icon", "02d"), 108, 13)
    t(f"{d['temp']}\xb0C", 2, 14)
    t(str(d["desc"])[:14], 2, 26)
    hline(37)
    t(f"Hum {d['humidity']}%", 2, 40)
    t(f"FL {d['feels_like']}\xb0", 68, 40)
    t(f"Wind {d['wind_kph']}kph", 2, 50)
    page_dots(state["page"], PAGE_COUNT)
    show()

def page_forecast():
    city  = CITIES[state["city"]]
    label = CITY_LABELS[state["city"]]
    d     = state["data"].get(f"forecast/{city}", _f())
    items = d.get("items", [])
    cls()
    header("FORECAST", label)
    t("Time   Tmp  Cond", 0, 13)
    hline(22)
    for i, item in enumerate(items[:4]):
        y = 24 + i * 9
        t(f"{item['time'][:5]} {str(item['temp']):>3}\xb0 {str(item['desc'])[:6]}", 0, y)
    page_dots(state["page"], PAGE_COUNT)
    show()

def page_aqi():
    city  = CITIES[state["city"]]
    label = CITY_LABELS[state["city"]]
    d     = state["data"].get(f"aqi/{city}", _a())
    aqi   = d.get("aqi", "--")
    cat   = str(d.get("category", ""))
    cls()
    header("AIR QUALITY", label)
    cx(f"Index: {aqi}", 16)
    cx(cat, 26)
    hline(36)
    # 5-segment bar
    for i in range(5):
        x      = 2 + i * 26
        filled = isinstance(aqi, int) and i < aqi
        t(str(i + 1), x + 6, 40)
        if filled:
            oled.fill_rect(x, 50, 20, 7, 1)
        else:
            oled.rect(x, 50, 20, 7, 1)
    page_dots(state["page"], PAGE_COUNT)
    show()

def page_indoor():
    d = state["data"].get("sensors/indoor", _i())
    cls()
    header("INDOOR", "Sensors")
    t(f"Temp:     {d['temp']}\xb0C", 2, 16)
    t(f"Humidity: {d['humidity']}%", 2, 28)
    t(f"Pressure: {d['pressure']} hPa", 2, 40)
    hline(52)
    t(str(d.get("source", ""))[:16], 2, 55)
    page_dots(state["page"], PAGE_COUNT)
    show()

RENDERERS = [page_weather, page_forecast, page_aqi, page_indoor]

def redraw():
    if not hud_active():
        RENDERERS[state["page"]]()
        state["dirty"] = False
    elif state["dirty"]:
        # HUD just expired — force full redraw
        state["dirty"] = True

# ─────────────────────────────────────────────
# BUTTON EVENTS
# ─────────────────────────────────────────────

buttons = ButtonHandler()

def handle_event(ev):
    if ev is None:
        return
    if ev == "A_SHORT":
        state["page"]  = (state["page"] + 1) % PAGE_COUNT
        state["dirty"] = True
        show_hud()
    elif ev == "A_LONG":
        state["page"]  = (state["page"] - 1) % PAGE_COUNT
        state["dirty"] = True
        show_hud()
    elif ev == "B_SHORT":
        state["city"]  = (state["city"] + 1) % len(CITIES)
        state["dirty"] = True
        show_hud()
    elif ev == "B_LONG":
        # Long B: show HUD only (no city change) — confirm current location
        show_hud()

# ─────────────────────────────────────────────
# MQTT
# ─────────────────────────────────────────────

mqtt_client    = None
_last_reconnect = 0
RECONNECT_MS   = 10_000

def on_message(topic, msg):
    key = topic.decode()
    if key.startswith(MQTT_BASE + "/"):
        key = key[len(MQTT_BASE) + 1:]
    try:
        state["data"][key] = json.loads(msg)
        state["dirty"]     = True
    except Exception as e:
        print(f"[MQTT] JSON err {key}: {e}")

def connect_mqtt():
    global mqtt_client
    try:
        mqtt_client = MQTTClient(CLIENT_ID, MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.set_callback(on_message)
        mqtt_client.connect()
        for city in CITIES:
            mqtt_client.subscribe(f"{MQTT_BASE}/weather/{city}")
            mqtt_client.subscribe(f"{MQTT_BASE}/forecast/{city}")
            mqtt_client.subscribe(f"{MQTT_BASE}/aqi/{city}")
        mqtt_client.subscribe(f"{MQTT_BASE}/sensors/indoor")
        print("[MQTT] Connected")
        return True
    except Exception as e:
        print(f"[MQTT] Fail: {e}")
        mqtt_client = None
        return False

def mqtt_tick():
    global mqtt_client, _last_reconnect
    if mqtt_client is None:
        now = time.ticks_ms()
        if time.ticks_diff(now, _last_reconnect) > RECONNECT_MS:
            _last_reconnect = now
            connect_mqtt()
        return
    try:
        mqtt_client.check_msg()
    except Exception as e:
        print(f"[MQTT] Error: {e}")
        mqtt_client = None

# ─────────────────────────────────────────────
# WIFI
# ─────────────────────────────────────────────

def connect_wifi():
    import network as nw
    wlan = nw.WLAN(nw.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return True
    cls(); cx("Connecting WiFi", 24); show()
    wlan.connect(WIFI_SSID, WIFI_PASS)
    for _ in range(30):
        if wlan.isconnected():
            return True
        time.sleep(0.5)
    return False

# ─────────────────────────────────────────────
# SPLASH / ERROR
# ─────────────────────────────────────────────

def splash():
    cls()
    cx("HomeStation", 10)
    hline(22)
    cx("Smart Display", 28)
    cx("v1.0", 42)
    show()

def error_screen(m1, m2=""):
    cls()
    oled.fill_rect(0, 0, WIDTH, 11, 1)
    t("  ERROR", 2, 2, 0)
    cx(m1, 22); cx(m2, 36)
    show()

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    splash()
    time.sleep(1)

    if not connect_wifi():
        error_screen("WiFi failed"); return

    if not connect_mqtt():
        error_screen("MQTT failed", MQTT_BROKER); return

    state["dirty"] = True
    redraw()
    print("[HomeStation] Running — A=page, B=city")

    while True:
        handle_event(buttons.tick())
        mqtt_tick()

        # HUD expired — repaint underlying page
        if not hud_active() and state["dirty"]:
            redraw()

        gc.collect()
        time.sleep_ms(40)

main()
