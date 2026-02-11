"""G Train arrivals at Greenpoint Ave - real-time MTA data."""

import math
import time
import threading
from datetime import datetime

from nyct_gtfs import NYCTFeed
from ledmatrix import Canvas, run

# --- Config ---
STOP_ID = "G26"
REFRESH_SEC = 30

# --- Colors (dimmed for LED matrix) ---
G_GREEN = (35, 60, 22)
WHITE = (70, 70, 70)
DIM_WHITE = (80, 80, 80)
DIM_GRAY = (40, 40, 40)
MED_GREEN = (30, 65, 20)
DIVIDER = (15, 35, 12)
AMBER = (100, 75, 0)

# --- Shared state ---
arrivals = {"north": [], "south": [], "updated": 0.0}


def fetch_loop():
    """Background thread: poll MTA feed every REFRESH_SEC seconds."""
    while True:
        try:
            feed = NYCTFeed("G")
            now = datetime.now()
            north = []
            south = []
            for trip in feed.trips:
                for stu in trip.stop_time_updates:
                    if stu.stop_id == f"{STOP_ID}N":
                        mins = (stu.arrival - now).total_seconds() / 60
                        if mins >= 0:
                            north.append(int(mins))
                    elif stu.stop_id == f"{STOP_ID}S":
                        mins = (stu.arrival - now).total_seconds() / 60
                        if mins >= 0:
                            south.append(int(mins))
            arrivals["north"] = sorted(north)[:3]
            arrivals["south"] = sorted(south)[:3]
            arrivals["updated"] = time.monotonic()
        except Exception:
            pass
        time.sleep(REFRESH_SEC)


def draw_arrivals(canvas: Canvas, y_next: int, y_later: int, times: list, t: float):
    """Draw arrival times for one direction."""
    if not times:
        canvas.text(2, y_next, "---", DIM_GRAY)
        return
    # Next train
    if times[0] == 0:
        if int(t * 2) % 2:
            canvas.text(2, y_next, "NOW", AMBER)
    else:
        canvas.text(2, y_next, f"{times[0]} MIN", G_GREEN)
    # Later trains
    if len(times) > 1:
        later = "  ".join(str(m) for m in times[1:])
        canvas.text(2, y_later, later, MED_GREEN)


def render(canvas: Canvas, t: float, frame: int) -> None:
    canvas.clear()

    # G bullet icon
    canvas.circle(31, 6, 5, G_GREEN, filled=True)
    canvas.text(30, 4, "G", WHITE)

    # Station name
    canvas.text(12, 13, "GREENPT AV", DIM_WHITE)

    # Divider
    canvas.line(2, 20, 61, 20, DIVIDER)

    # Northbound (Court Sq)
    canvas.text(1, 23, "CT SQ", DIM_GRAY)
    draw_arrivals(canvas, 29, 35, arrivals["north"], t)

    # Divider
    canvas.line(2, 41, 61, 41, DIVIDER)

    # Southbound (Church Av)
    canvas.text(1, 44, "CHURCH AV", DIM_GRAY)
    draw_arrivals(canvas, 50, 56, arrivals["south"], t)

    # Status dot
    age = t - arrivals["updated"] if arrivals["updated"] else 999
    if age < 60:
        b = int(40 + 40 * abs(math.sin(t * 2)))
        canvas.set(1, 62, (0, b, 0))
    else:
        b = int(40 + 40 * abs(math.sin(t * 2)))
        canvas.set(1, 62, (b, b // 2, 0))


# Start background fetcher
threading.Thread(target=fetch_loop, daemon=True).start()

if __name__ == "__main__":
    run(render, fps=10, title="G Train - Greenpoint Av")
