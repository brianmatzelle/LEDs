"""Hello World - bouncing text with color cycling background."""

import math
from ledmatrix import Canvas, run


def render(canvas: Canvas, t: float, frame: int) -> None:
    canvas.clear()

    # Pulsing background border
    pulse = int((math.sin(t * 2) + 1) * 40)
    canvas.rect(0, 0, 64, 64, (pulse, 0, pulse), filled=False)

    # Bouncing "HELLO" text
    x = int(20 + math.sin(t * 1.5) * 10)
    y = int(28 + math.sin(t * 2.3) * 8)
    hue = (t * 50) % 360
    canvas.text(x, y, "HELLO", canvas.hsv(hue))

    # Frame counter in corner
    canvas.text(1, 1, str(frame % 1000), (80, 80, 80))


if __name__ == "__main__":
    run(render, fps=30, title="Hello World")
