"""
sensor_test.py — Standalone sensor verification for Pi 4
Run this BEFORE main.py to confirm DHT11 and BMP180 are working.

Usage:
  python3 sensor_test.py

Dependencies:
  pip install adafruit-circuitpython-dht adafruit-circuitpython-bmp180
  sudo apt install libgpiod2
"""

import time

# ── DHT11 ────────────────────────────────────────────────────────────────────
print("=" * 40)
print("Testing DHT11 (GPIO4)...")
try:
    import board
    import adafruit_dht
    dht = adafruit_dht.DHT11(board.D4)

    # DHT11 sometimes takes a few tries on first read
    for attempt in range(5):
        try:
            temp = dht.temperature
            hum  = dht.humidity
            print(f"  ✓ Temperature: {temp}°C")
            print(f"  ✓ Humidity:    {hum}%")
            break
        except RuntimeError as e:
            print(f"  Attempt {attempt+1}/5: {e}")
            time.sleep(2)
    else:
        print("  ✗ DHT11 failed after 5 attempts — check wiring")
    dht.exit()
except ImportError:
    print("  ✗ adafruit-circuitpython-dht not installed")
except Exception as e:
    print(f"  ✗ DHT11 error: {e}")

# ── BMP180 ───────────────────────────────────────────────────────────────────
print()
print("Testing BMP180 (I2C: SDA=GPIO2, SCL=GPIO3)...")
try:
    import busio
    import adafruit_bmp180

    i2c = busio.I2C(board.SCL, board.SDA)
    bmp = adafruit_bmp180.Adafruit_BMP180_I2C(i2c)

    temp     = round(bmp.temperature, 1)
    pressure = round(bmp.pressure, 2)
    print(f"  ✓ Temperature: {temp}°C")
    print(f"  ✓ Pressure:    {pressure} hPa")
except ImportError:
    print("  ✗ adafruit-circuitpython-bmp180 not installed")
except Exception as e:
    print(f"  ✗ BMP180 error: {e}")
    print("  Hint: check I2C address with: sudo i2cdetect -y 1")

print()
print("=" * 40)
print("Done. If sensors passed, run main.py")
