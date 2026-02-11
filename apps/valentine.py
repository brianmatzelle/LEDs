"""Valentine's Day - pulsing heart with a love note."""

import math
from ledmatrix import Canvas, run


def _in_heart(px, py, cx, cy, size):
    """Check if pixel is inside a heart shape using the implicit curve."""
    nx = (px - cx) / size
    ny = -(py - cy) / size + 0.3
    v = (nx * nx + ny * ny - 1) ** 3 - nx * nx * ny * ny * ny
    return v <= 0


# Tiny 5x4 heart bitmap for floating particles
_MINI_HEART = [
    [0, 1, 0, 1, 0],
    [1, 1, 1, 1, 1],
    [0, 1, 1, 1, 0],
    [0, 0, 1, 0, 0],
]


def render(canvas: Canvas, t: float, frame: int) -> None:
    canvas.clear()

    # --- Main pulsing heart (heartbeat pattern) ---
    beat_t = (t * 1.3) % 1.0
    if beat_t < 0.15:
        pulse = 1.0 + 0.12 * math.sin(beat_t / 0.15 * math.pi)
    elif 0.2 < beat_t < 0.35:
        pulse = 1.0 + 0.08 * math.sin((beat_t - 0.2) / 0.15 * math.pi)
    else:
        pulse = 1.0

    size = 8.0 * pulse
    cx, cy = 32, 13
    glow = 0.85 + 0.15 * max(0, (pulse - 1.0) / 0.12)

    for y in range(0, 26):
        for x in range(16, 48):
            if _in_heart(x, y, cx, cy, size):
                d = math.hypot(x - cx, y - cy)
                shade = max(0.35, 1.0 - d / 18)
                r = int(min(255, 240 * shade * glow))
                g = int(15 * shade * glow)
                b = int(40 * shade * glow)
                canvas.set(x, y, (r, g, b))

    # --- Text ---
    pink = (255, 110, 150)

    # "TO: GIANNA" - 10 chars, width=39, centered at x=13
    canvas.text(13, 29, "TO: GIANNA", pink)

    # "FROM: BRIAN" - 11 chars, width=43, centered at x=11
    canvas.text(11, 37, "FROM: BRIAN", pink)

    # "I LUV U" - color cycling, width=27, centered at x=19
    hue = (t * 35) % 360
    canvas.text(19, 48, "I LUV U", Canvas.hsv(hue, 0.4, 1.0))

    # --- Floating mini hearts ---
    for i in range(6):
        speed = 5 + i * 1.2
        x_base = 4 + i * 11
        hx = int(x_base + math.sin(t * 0.6 + i * 1.7) * 3)
        hy = 63 - int((t * speed + i * 17) % 80)
        alpha = min(1.0, max(0, (hy + 4) / 10))
        if alpha < 0.1:
            continue
        r = int(160 * alpha)
        g = int(30 * alpha)
        b = int(50 * alpha)
        for dy, row in enumerate(_MINI_HEART):
            for dx, on in enumerate(row):
                if on:
                    canvas.set(hx + dx, hy + dy, (r, g, b))


if __name__ == "__main__":
    run(render, fps=30, title="Valentine")
