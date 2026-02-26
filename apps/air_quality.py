"""Air quality monitor - live sensor data from room-monitor API."""

import math
import time
import threading
import urllib.request
import json

from ledmatrix import Canvas, run
from ledmatrix.canvas import _FONT_3X5

# --- Config ---
API_URL = "http://192.168.1.190:8080"
REFRESH_SEC = 30
CYCLE_SEC = 10  # seconds per metric page

# --- Colors (dimmed for LED panels) ---
GREEN = (12, 45, 12)
YELLOW = (50, 45, 4)
RED = (50, 10, 6)
BLUE = (8, 18, 50)

GREEN_V = (25, 85, 25)
YELLOW_V = (95, 85, 8)
RED_V = (95, 25, 15)
BLUE_V = (18, 40, 95)

DIM_WHITE = (50, 50, 50)
DIM_GRAY = (25, 25, 25)
DARK_GRAY = (12, 12, 12)

STYLES = {
    "good":  {"bar": GREEN,  "val": GREEN_V},
    "fair":  {"bar": YELLOW, "val": YELLOW_V},
    "poor":  {"bar": RED,    "val": RED_V},
    "cold":  {"bar": BLUE,   "val": BLUE_V},
    "hot":   {"bar": RED,    "val": RED_V},
    "dry":   {"bar": YELLOW, "val": YELLOW_V},
    "humid": {"bar": YELLOW, "val": YELLOW_V},
}

LABELS = {
    "good": "GOOD", "fair": "FAIR", "poor": "POOR",
    "cold": "COLD", "hot": "HOT", "dry": "DRY", "humid": "HUMID",
}

# --- Metric definitions ---
METRICS = [
    {
        "key": "co2", "label": "CO2", "unit": "PPM",
        "fmt": lambda v: str(int(v)),
        "level": lambda v: "good" if v < 800 else ("fair" if v < 1000 else "poor"),
    },
    {
        "key": "temperature", "label": "TEMP", "unit": "C",
        "fmt": lambda v: f"{v:.1f}",
        "level": lambda v: "cold" if v < 18 else ("good" if v < 26 else "hot"),
    },
    {
        "key": "relative_humidity", "label": "HUMIDITY", "unit": "",
        "fmt": lambda v: f"{v:.0f}",
        "level": lambda v: "dry" if v < 30 else ("good" if v < 60 else "humid"),
    },
    {
        "key": "mass_concentration_pm2p5", "label": "PM 2.5", "unit": "",
        "fmt": lambda v: f"{v:.1f}",
        "level": lambda v: "good" if v < 12 else ("fair" if v < 35 else "poor"),
    },
    {
        "key": "voc_index", "label": "VOC", "unit": "INDEX",
        "fmt": lambda v: str(int(v)),
        "level": lambda v: "good" if v < 150 else ("fair" if v < 250 else "poor"),
    },
    {
        "key": "nox_index", "label": "NOX", "unit": "INDEX",
        "fmt": lambda v: str(int(v)),
        "level": lambda v: "good" if v < 20 else ("fair" if v < 150 else "poor"),
    },
]

# --- Shared state ---
sensor = {
    "co2": 0, "temperature": 0.0, "relative_humidity": 0.0,
    "mass_concentration_pm2p5": 0.0, "voc_index": 0.0, "nox_index": 0.0,
    "updated": 0.0, "error": False,
}


def _fetch_loop():
    """Background thread: poll room-monitor API."""
    while True:
        try:
            req = urllib.request.Request(
                f"{API_URL}/data?limit=1",
                headers={"User-Agent": "LedMatrix/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            if data:
                r = data[0]
                for m in METRICS:
                    sensor[m["key"]] = r.get(m["key"], 0)
                sensor["updated"] = time.monotonic()
                sensor["error"] = False
        except Exception as e:
            print(f"[air] fetch error: {e}")
            sensor["error"] = True
        time.sleep(REFRESH_SEC)


# --- Drawing helpers ---

def _big_text(canvas, x, y, text, color, scale=3):
    """Draw text at scaled-up size using the 3x5 bitmap font."""
    cx = x
    for ch in text.upper():
        glyph = _FONT_3X5.get(ch)
        if glyph is None:
            cx += (3 + 1) * scale
            continue
        for ry, bits in enumerate(glyph):
            for col in range(3):
                if bits & (1 << (2 - col)):
                    px, py = cx + col * scale, y + ry * scale
                    for dy in range(scale):
                        for dx in range(scale):
                            canvas.set(px + dx, py + dy, color)
        cx += (3 + 1) * scale


def _big_width(text, scale=3):
    """Pixel width of scaled text."""
    n = len(text)
    return n * (3 + 1) * scale - scale if n else 0


def _center_big(canvas, y, text, color, scale=3):
    """Draw big text centered horizontally."""
    w = _big_width(text, scale)
    _big_text(canvas, max(0, (64 - w) // 2), y, text, color, scale)


def _center(canvas, y, text, color):
    """Draw regular 3x5 text centered horizontally."""
    w = len(text) * 4 - 1
    canvas.text(max(0, (64 - w) // 2), y, text, color)


# --- Render ---

def render(canvas: Canvas, t: float, frame: int) -> None:
    canvas.clear()

    # Loading state
    if sensor["updated"] == 0.0:
        _center(canvas, 25, "AIR QUALITY", DIM_GRAY)
        _center(canvas, 33, "WAITING", DIM_GRAY)
        dots = "." * (int(t * 2) % 4)
        _center(canvas, 41, dots, DIM_GRAY)
        return

    # Current metric
    idx = int(t / CYCLE_SEC) % len(METRICS)
    m = METRICS[idx]
    val = sensor[m["key"]]
    level = m["level"](val)
    sty = STYLES[level]
    val_str = m["fmt"](val)
    scale = 3 if _big_width(val_str, 3) <= 60 else 2
    val_h = 5 * scale

    # Transition: color bar pulses taller on page change
    page_t = t % CYCLE_SEC
    if page_t < 0.3:
        bar_h = 3 + int((1 - page_t / 0.3) * 5)
    else:
        bar_h = 3

    # Top color bar
    canvas.rect(0, 0, 64, bar_h, sty["bar"])

    # Label
    _center(canvas, 5, m["label"], DIM_WHITE)

    # Value (big)
    _center_big(canvas, 14, val_str, sty["val"], scale)

    # Unit
    if m["unit"]:
        _center(canvas, 14 + val_h + 2, m["unit"], DIM_GRAY)

    # Quality label
    _center(canvas, 42, LABELS[level], sty["bar"])

    # Progress bar
    progress = (t % CYCLE_SEC) / CYCLE_SEC
    bar_w = int(progress * 60)
    if bar_w > 0:
        canvas.rect(2, 52, bar_w, 1, DARK_GRAY)

    # Metric position dots
    n = len(METRICS)
    dot_w = n * 3 - 1
    sx = (64 - dot_w) // 2
    for i in range(n):
        c = sty["bar"] if i == idx else DARK_GRAY
        dx = sx + i * 3
        canvas.set(dx, 56, c)
        canvas.set(dx + 1, 56, c)

    # Status dot (uses monotonic time directly for correct staleness)
    now = time.monotonic()
    age = now - sensor["updated"] if sensor["updated"] else 999
    if sensor["error"]:
        b = int(40 + 40 * abs(math.sin(t * 2)))
        canvas.set(1, 62, (b, 0, 0))
    elif age < 90:
        b = int(30 + 30 * abs(math.sin(t * 2)))
        canvas.set(1, 62, (0, b, 0))
    else:
        b = int(40 + 40 * abs(math.sin(t * 2)))
        canvas.set(1, 62, (b, b // 2, 0))


# Start background fetcher
threading.Thread(target=_fetch_loop, daemon=True).start()

if __name__ == "__main__":
    run(render, fps=10, title="Air Quality Monitor")
