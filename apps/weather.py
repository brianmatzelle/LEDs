"""Weather app for Greenpoint, Brooklyn — animated weather on the LED matrix."""

import math
import random
import threading
import time
import json
import urllib.request
from ledmatrix import Canvas, run

# Greenpoint, Brooklyn coordinates
LAT, LON = 40.7274, -73.9514
POLL_INTERVAL = 300  # 5 minutes

# Shared state updated by background thread
weather_data = {
    "temp_f": None,
    "code": None,       # WMO weather code
    "desc": "",
    "updated": 0.0,
}

# WMO weather codes → description and theme
_WMO = {
    0: ("CLEAR", "clear"),
    1: ("MOSTLY CLEAR", "clear"),
    2: ("PARTLY CLOUDY", "cloudy"),
    3: ("OVERCAST", "cloudy"),
    45: ("FOG", "fog"),
    48: ("RIME FOG", "fog"),
    51: ("LIGHT DRIZZLE", "rain"),
    53: ("DRIZZLE", "rain"),
    55: ("HEAVY DRIZZLE", "rain"),
    56: ("FREEZING DRIZZLE", "rain"),
    57: ("HEAVY FRZ DRIZZLE", "rain"),
    61: ("LIGHT RAIN", "rain"),
    63: ("RAIN", "rain"),
    65: ("HEAVY RAIN", "rain"),
    66: ("FREEZING RAIN", "rain"),
    67: ("HEAVY FRZ RAIN", "rain"),
    71: ("LIGHT SNOW", "snow"),
    73: ("SNOW", "snow"),
    75: ("HEAVY SNOW", "snow"),
    77: ("SNOW GRAINS", "snow"),
    80: ("LIGHT SHOWERS", "rain"),
    81: ("SHOWERS", "rain"),
    82: ("HEAVY SHOWERS", "rain"),
    85: ("LIGHT SNOW SHWRS", "snow"),
    86: ("HEAVY SNOW SHWRS", "snow"),
    95: ("THUNDERSTORM", "storm"),
    96: ("T-STORM W/ HAIL", "storm"),
    99: ("SEVERE T-STORM", "storm"),
}

# --- Particle systems (seeded once, updated each frame) ---

_snowflakes = []  # list of [x, y, speed, drift_phase, size]
_raindrops = []   # list of [x, y, speed, length]
_stars = []       # list of [x, y, twinkle_phase]
_lightning_flash = 0.0


def _init_particles():
    global _snowflakes, _raindrops, _stars
    _snowflakes = [
        [random.uniform(0, 63), random.uniform(0, 63),
         random.uniform(4, 10), random.uniform(0, 6.28), random.choice([1, 1, 2])]
        for _ in range(35)
    ]
    _raindrops = [
        [random.uniform(0, 63), random.uniform(0, 63),
         random.uniform(25, 45), random.randint(2, 4)]
        for _ in range(40)
    ]
    _stars = [
        [random.randint(0, 63), random.randint(0, 30),
         random.uniform(0, 6.28)]
        for _ in range(25)
    ]


_init_particles()


def _fetch_weather():
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        f"&current=temperature_2m,weather_code"
        f"&temperature_unit=fahrenheit"
        f"&timezone=America%2FNew_York"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "LEDs/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    cur = data["current"]
    code = int(cur["weather_code"])
    desc, _ = _WMO.get(code, ("UNKNOWN", "clear"))
    weather_data["temp_f"] = round(cur["temperature_2m"])
    weather_data["code"] = code
    weather_data["desc"] = desc
    weather_data["updated"] = time.monotonic()


def _fetch_loop():
    while True:
        try:
            _fetch_weather()
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)


threading.Thread(target=_fetch_loop, daemon=True).start()


def _theme():
    code = weather_data.get("code")
    if code is None:
        return "clear"
    return _WMO.get(code, ("", "clear"))[1]


# --- Drawing helpers ---

_SUN_BITMAP = [
    [0, 0, 0, 1, 1, 0, 0, 0],
    [0, 0, 1, 1, 1, 1, 0, 0],
    [0, 1, 1, 1, 1, 1, 1, 0],
    [1, 1, 1, 1, 1, 1, 1, 1],
    [1, 1, 1, 1, 1, 1, 1, 1],
    [0, 1, 1, 1, 1, 1, 1, 0],
    [0, 0, 1, 1, 1, 1, 0, 0],
    [0, 0, 0, 1, 1, 0, 0, 0],
]

_CLOUD_BITMAP = [
    [0, 0, 1, 1, 1, 0, 0, 0, 0, 0],
    [0, 1, 1, 1, 1, 1, 1, 1, 0, 0],
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 0],
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    [0, 1, 1, 1, 1, 1, 1, 1, 1, 0],
]


def _draw_sun(canvas, cx, cy, t):
    glow = 0.9 + 0.1 * math.sin(t * 2)
    for dy, row in enumerate(_SUN_BITMAP):
        for dx, on in enumerate(row):
            if on:
                px, py = cx - 4 + dx, cy - 4 + dy
                d = math.hypot(dx - 3.5, dy - 3.5)
                shade = max(0.5, 1.0 - d / 6)
                r = int(255 * shade * glow)
                g = int(200 * shade * glow)
                b = int(30 * shade * glow)
                canvas.set(px, py, (r, g, b))
    # rays
    for i in range(8):
        angle = (i / 8) * 6.28 + t * 0.5
        ray_len = 4 + 1.5 * math.sin(t * 3 + i)
        ex = cx + math.cos(angle) * ray_len
        ey = cy + math.sin(angle) * ray_len
        sx = cx + math.cos(angle) * 5
        sy = cy + math.sin(angle) * 5
        canvas.line(int(sx), int(sy), int(ex), int(ey), (255, 180, 20))


def _draw_cloud(canvas, cx, cy, color):
    for dy, row in enumerate(_CLOUD_BITMAP):
        for dx, on in enumerate(row):
            if on:
                canvas.set(cx - 5 + dx, cy - 2 + dy, color)


def _draw_snow(canvas, t, dt):
    for s in _snowflakes:
        s[1] += s[2] * dt
        s[0] += math.sin(t * 1.5 + s[3]) * 0.3
        if s[1] > 63:
            s[1] = -2
            s[0] = random.uniform(0, 63)
        ix, iy = int(s[0]), int(s[1])
        canvas.set(ix, iy, (200, 210, 255))
        if s[4] == 2:
            canvas.set(ix + 1, iy, (180, 190, 235))
            canvas.set(ix, iy + 1, (180, 190, 235))


def _draw_rain(canvas, t, dt):
    for r in _raindrops:
        r[1] += r[2] * dt
        if r[1] > 63:
            r[1] = random.uniform(-8, -2)
            r[0] = random.uniform(0, 63)
        ix, iy = int(r[0]), int(r[1])
        for i in range(r[3]):
            canvas.set(ix, iy - i, (100, 130, 220))


def _draw_stars(canvas, t):
    for s in _stars:
        brightness = 0.4 + 0.6 * max(0, math.sin(t * 1.2 + s[2]))
        v = int(180 * brightness)
        canvas.set(s[0], s[1], (v, v, int(v * 0.9)))


def _draw_fog(canvas, t):
    for band in range(3):
        y_base = 8 + band * 10
        offset = math.sin(t * 0.4 + band * 2) * 6
        for x in range(64):
            density = 0.2 + 0.15 * math.sin((x + offset) * 0.15)
            v = int(120 * density)
            canvas.set(x, y_base, (v, v, v))
            canvas.set(x, y_base + 1, (v, v, v))


def _draw_lightning(canvas, t):
    global _lightning_flash
    if random.random() < 0.005:
        _lightning_flash = t
    if t - _lightning_flash < 0.15:
        brightness = int(200 * (1.0 - (t - _lightning_flash) / 0.15))
        for y in range(28):
            for x in range(64):
                c = canvas.get(x, y)
                r = min(255, c[0] + brightness)
                g = min(255, c[1] + brightness)
                b = min(255, c[2] + int(brightness * 0.7))
                canvas.set(x, y, (r, g, b))


# --- Theme colors ---

def _bg_color(theme):
    return {
        "clear": (2, 2, 12),
        "cloudy": (10, 12, 18),
        "rain": (5, 5, 15),
        "snow": (8, 10, 18),
        "fog": (12, 14, 16),
        "storm": (3, 3, 10),
    }.get(theme, (2, 2, 12))


def _text_color(theme):
    return {
        "clear": (255, 220, 80),
        "cloudy": (180, 190, 210),
        "rain": (100, 160, 255),
        "snow": (200, 220, 255),
        "fog": (160, 170, 180),
        "storm": (220, 180, 255),
    }.get(theme, (200, 200, 200))


_last_t = 0.0


def render(canvas: Canvas, t: float, frame: int) -> None:
    global _last_t
    dt = t - _last_t if _last_t else 0.016
    _last_t = t

    theme = _theme()
    canvas.clear(_bg_color(theme))

    # --- Animated background ---
    if theme == "clear":
        _draw_stars(canvas, t)
        _draw_sun(canvas, 32, 12, t)
    elif theme == "cloudy":
        _draw_cloud(canvas, 18, 8, (80, 85, 100))
        _draw_cloud(canvas, 40, 5, (100, 105, 120))
        _draw_cloud(canvas, 52, 10, (70, 75, 90))
    elif theme == "rain":
        _draw_cloud(canvas, 16, 5, (60, 65, 80))
        _draw_cloud(canvas, 42, 3, (70, 75, 90))
        _draw_rain(canvas, t, dt)
    elif theme == "snow":
        _draw_cloud(canvas, 20, 5, (100, 105, 120))
        _draw_cloud(canvas, 45, 3, (90, 95, 110))
        _draw_snow(canvas, t, dt)
    elif theme == "fog":
        _draw_fog(canvas, t)
    elif theme == "storm":
        _draw_cloud(canvas, 16, 5, (40, 42, 55))
        _draw_cloud(canvas, 42, 3, (50, 52, 65))
        _draw_rain(canvas, t, dt)
        _draw_lightning(canvas, t)

    # --- Temperature display ---
    temp = weather_data.get("temp_f")
    tc = _text_color(theme)

    if temp is not None:
        temp_str = f"{temp}F"
        # Center the temp text (each char ~4px wide)
        tw = len(temp_str) * 4 - 1
        tx = (64 - tw) // 2
        canvas.text(tx, 30, temp_str, tc)
    else:
        canvas.text(16, 30, "LOADING", (120, 120, 120))

    # --- Condition text ---
    desc = weather_data.get("desc", "")
    if desc:
        # Scroll long text, static short text
        dw = len(desc) * 4 - 1
        if dw <= 64:
            dx = (64 - dw) // 2
            canvas.text(dx, 40, desc, tc)
        else:
            # scrolling
            scroll_speed = 20
            total_w = dw + 20
            offset = int(t * scroll_speed) % total_w
            dx = 64 - offset
            canvas.text(dx, 40, desc, tc)

    # --- Location label ---
    loc = "GREENPOINT"
    lw = len(loc) * 4 - 1
    lx = (64 - lw) // 2
    hue = (t * 25) % 360
    canvas.text(lx, 52, loc, Canvas.hsv(hue, 0.35, 0.85))

    # --- Status dot (bottom-left, like sports.py) ---
    age = time.monotonic() - weather_data.get("updated", 0)
    if weather_data["temp_f"] is None:
        # loading — pulsing yellow
        v = int(40 + 40 * abs(math.sin(t * 2)))
        canvas.set(1, 62, (v, v, 0))
    elif age < POLL_INTERVAL * 2:
        # fresh — pulsing green
        v = int(20 + 20 * abs(math.sin(t * 1.5)))
        canvas.set(1, 62, (0, v, 0))
    else:
        # stale — pulsing orange
        v = int(30 + 30 * abs(math.sin(t * 1.5)))
        canvas.set(1, 62, (v, int(v * 0.5), 0))


if __name__ == "__main__":
    run(render, fps=30, title="Weather")
