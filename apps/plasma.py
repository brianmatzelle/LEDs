"""Plasma effect - classic demoscene sine-based color plasma."""

import math
from ledmatrix import Canvas, run


def render(canvas: Canvas, t: float, frame: int) -> None:
    for y in range(canvas.height):
        for x in range(canvas.width):
            # Classic plasma formula with multiple sine waves
            v1 = math.sin(x * 0.1 + t)
            v2 = math.sin(y * 0.1 + t * 0.7)
            v3 = math.sin((x + y) * 0.1 + t * 0.5)
            v4 = math.sin(math.sqrt(x * x + y * y) * 0.1 + t * 1.3)

            v = (v1 + v2 + v3 + v4) / 4.0  # -1 to 1
            hue = (v * 180 + t * 30) % 360
            canvas.set(x, y, canvas.hsv(hue, 0.9, 0.7))


if __name__ == "__main__":
    run(render, fps=20, title="Plasma")
