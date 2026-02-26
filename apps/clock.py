"""Analog + digital clock with smooth hands and date display."""

import math
import time
from ledmatrix import Canvas, run

# Larger 5x7 digit font for the time display
_DIGITS_5X7 = {
    '0': [0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110],
    '1': [0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
    '2': [0b01110, 0b10001, 0b00001, 0b00110, 0b01000, 0b10000, 0b11111],
    '3': [0b01110, 0b10001, 0b00001, 0b00110, 0b00001, 0b10001, 0b01110],
    '4': [0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010],
    '5': [0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110],
    '6': [0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110],
    '7': [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000],
    '8': [0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110],
    '9': [0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100],
}

DAYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
          'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

# Clock face geometry
CX, CY = 31, 26
RADIUS = 18


def _draw_big_digit(canvas, x, y, ch, color):
    """Draw a 5x7 digit at (x, y)."""
    glyph = _DIGITS_5X7.get(ch)
    if glyph is None:
        return
    for row_idx, row_bits in enumerate(glyph):
        for col in range(5):
            if row_bits & (1 << (4 - col)):
                canvas.set(x + col, y + row_idx, color)


def _draw_big_time(canvas, hour, minute, y, color):
    """Draw HH:MM in large 5x7 digits, centered horizontally."""
    h1 = str(hour).zfill(2)
    m1 = str(minute).zfill(2)
    # Total width: 4 digits * 5px + 2 gaps * 1px + colon 1px = 23px
    # But let's space it: 5+1 + 5+2 + 1+2 + 5+1 + 5 = 27px... let me calculate
    # digit(5) gap(1) digit(5) gap(2) colon(1) gap(2) digit(5) gap(1) digit(5) = 27
    total_w = 27
    sx = (64 - total_w) // 2
    _draw_big_digit(canvas, sx, y, h1[0], color)
    _draw_big_digit(canvas, sx + 6, y, h1[1], color)
    # Colon - two dots
    cx = sx + 13
    canvas.set(cx, y + 2, color)
    canvas.set(cx, y + 4, color)
    _draw_big_digit(canvas, sx + 16, y, m1[0], color)
    _draw_big_digit(canvas, sx + 22, y, m1[1], color)


def _hand(canvas, angle_deg, length, color, thick=False):
    """Draw a clock hand from center at the given angle and length."""
    # 0 degrees = 12 o'clock, clockwise
    rad = math.radians(angle_deg - 90)
    ex = CX + math.cos(rad) * length
    ey = CY + math.sin(rad) * length
    canvas.line(CX, CY, int(ex), int(ey), color)
    if thick:
        # Draw adjacent lines for thickness
        perp = rad + math.pi / 2
        dx, dy = math.cos(perp) * 0.6, math.sin(perp) * 0.6
        canvas.line(int(CX + dx), int(CY + dy), int(ex + dx), int(ey + dy), color)
        canvas.line(int(CX - dx), int(CY - dy), int(ex - dx), int(ey - dy), color)


def render(canvas: Canvas, t: float, frame: int) -> None:
    canvas.clear()

    now = time.localtime()
    hour = now.tm_hour % 12
    minute = now.tm_min
    second = now.tm_sec
    frac = time.time() % 1  # fractional second for smooth motion

    # --- Analog clock face ---

    # Outer circle
    canvas.circle(CX, CY, RADIUS, (30, 30, 60), filled=False)

    # Hour tick marks
    for i in range(12):
        angle = math.radians(i * 30 - 90)
        inner = RADIUS - 3 if i % 3 == 0 else RADIUS - 2
        outer = RADIUS - 1
        x0 = int(CX + math.cos(angle) * inner)
        y0 = int(CY + math.sin(angle) * inner)
        x1 = int(CX + math.cos(angle) * outer)
        y1 = int(CY + math.sin(angle) * outer)
        tick_color = (100, 100, 140) if i % 3 == 0 else (50, 50, 80)
        canvas.line(x0, y0, x1, y1, tick_color)

    # Minute dots (every 5 minutes, skip hour marks)
    for i in range(60):
        if i % 5 == 0:
            continue
        angle = math.radians(i * 6 - 90)
        px = int(CX + math.cos(angle) * (RADIUS - 1))
        py = int(CY + math.sin(angle) * (RADIUS - 1))
        canvas.set(px, py, (20, 20, 35))

    # Hour hand
    hour_angle = (hour + minute / 60) * 30
    _hand(canvas, hour_angle, RADIUS * 0.5, (200, 200, 220), thick=True)

    # Minute hand
    min_angle = (minute + second / 60) * 6
    _hand(canvas, min_angle, RADIUS * 0.75, (100, 180, 255), thick=True)

    # Second hand (smooth sweep)
    sec_angle = (second + frac) * 6
    _hand(canvas, sec_angle, RADIUS * 0.85, (255, 60, 60))

    # Center dot
    canvas.circle(CX, CY, 1, (255, 255, 255), filled=True)

    # --- Digital time (big digits below clock face) ---
    display_hour = now.tm_hour % 12 or 12
    time_color = (140, 160, 200)
    _draw_big_time(canvas, display_hour, minute, 50, time_color)

    # AM/PM indicator
    ampm = "AM" if now.tm_hour < 12 else "PM"
    canvas.text(50, 52, ampm, (80, 80, 100))

    # Seconds in small font, right side
    canvas.text(50, 58, str(second).zfill(2), (80, 60, 60))

    # --- Date along the bottom ---
    day_name = DAYS[now.tm_wday]
    date_str = f"{day_name} {MONTHS[now.tm_mon - 1]} {now.tm_mday}"
    # Center the date text (each char is 4px wide with spacing)
    date_w = len(date_str) * 4 - 1
    date_x = (64 - date_w) // 2
    canvas.text(date_x, 1, date_str, (50, 60, 50))


if __name__ == "__main__":
    run(render, fps=30, title="Clock")
