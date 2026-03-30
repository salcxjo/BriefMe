"""
buttons.py — Button handler for Waveshare 1.3" OLED Pico board

Button A (GP15): short press = next page, long press = prev page
Button B (GP17): short press = next city, long press = show HUD

Integrate into main.py:
    from buttons import ButtonHandler
    buttons = ButtonHandler()
    # in loop:
    event = buttons.tick()
    if event == "A_SHORT": ...
    elif event == "A_LONG": ...
    elif event == "B_SHORT": ...
    elif event == "B_LONG": ...
"""

import time
from machine import Pin

DEBOUNCE_MS  = 30    # ignore bounces shorter than this
LONG_PRESS_MS = 600  # hold duration for "long press"

class ButtonHandler:
    def __init__(self, pin_a=15, pin_b=17):
        self.btn_a = Pin(pin_a, Pin.IN, Pin.PULL_UP)
        self.btn_b = Pin(pin_b, Pin.IN, Pin.PULL_UP)

        self._a_down  = False
        self._b_down  = False
        self._a_since = 0
        self._b_since = 0
        self._a_fired = False  # long press already emitted
        self._b_fired = False

    def tick(self) -> str | None:
        """Call every loop iteration. Returns event string or None."""
        now = time.ticks_ms()
        event = None

        # ── Button A ──────────────────────────────────────────────
        a_pressed = self.btn_a.value() == 0

        if a_pressed and not self._a_down:
            # Just pressed
            self._a_down  = True
            self._a_since = now
            self._a_fired = False

        elif a_pressed and self._a_down and not self._a_fired:
            # Still held — check for long press
            if time.ticks_diff(now, self._a_since) >= LONG_PRESS_MS:
                self._a_fired = True
                event = "A_LONG"

        elif not a_pressed and self._a_down:
            # Released
            self._a_down = False
            if not self._a_fired:
                held = time.ticks_diff(now, self._a_since)
                if held >= DEBOUNCE_MS:
                    event = "A_SHORT"

        # ── Button B ──────────────────────────────────────────────
        b_pressed = self.btn_b.value() == 0

        if b_pressed and not self._b_down:
            self._b_down  = True
            self._b_since = now
            self._b_fired = False

        elif b_pressed and self._b_down and not self._b_fired:
            if time.ticks_diff(now, self._b_since) >= LONG_PRESS_MS:
                self._b_fired = True
                event = "B_LONG"

        elif not b_pressed and self._b_down:
            self._b_down = False
            if not self._b_fired:
                held = time.ticks_diff(now, self._b_since)
                if held >= DEBOUNCE_MS:
                    event = "B_SHORT"

        return event
